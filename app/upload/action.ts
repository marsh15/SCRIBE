"use server";

import pdf from "pdf-parse";
import { parse as csvParse } from "csv-parse/sync";
import * as mammoth from "mammoth";
import { db } from "@/lib/db-config";
import { files, documents } from "@/lib/db-schema";
import { generateEmbeddings } from "@/lib/embeddings";
import { chunkContent } from "@/lib/chunking";
import { revalidatePath } from "next/cache";
import { getUserId } from "@/lib/auth";

const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB

export async function processDocument(formData: FormData) {
    try {
        const userId = await getUserId();

        const file = formData.get("file") as File;
        if (!file) {
            return { success: false, error: "No file uploaded" };
        }

        // Validate file size upfront
        if (file.size > MAX_FILE_SIZE) {
            return {
                success: false,
                error: `File too large (${(file.size / (1024 * 1024)).toFixed(1)} MB). Maximum size is 10 MB.`,
            };
        }

        const bytes = await file.arrayBuffer();
        const buffer = Buffer.from(bytes);
        let extractedText = "";

        // Extract text based on file type
        const type = file.type;
        const name = file.name;
        const extension = name.split('.').pop()?.toLowerCase();

        try {
            if (type === "application/pdf" || extension === "pdf") {
                const data = await pdf(buffer);
                extractedText = data.text;
            } else if (
                type === "text/plain" ||
                type === "text/markdown" ||
                extension === "txt" ||
                extension === "md"
            ) {
                extractedText = buffer.toString("utf-8");
            } else if (type === "text/csv" || extension === "csv") {
                const records = csvParse(buffer.toString("utf-8"), { skip_empty_lines: true });
                extractedText = records.map((row: any[]) => row.join(" ")).join("\n");
            } else if (
                type === "application/vnd.openxmlformats-officedocument.wordprocessingml.document" ||
                extension === "docx"
            ) {
                const data = await mammoth.extractRawText({ buffer });
                extractedText = data.value;
            } else {
                return { success: false, error: `Unsupported file type: .${extension || type}` };
            }
        } catch (extractionError: any) {
            console.error("Text extraction error:", extractionError);
            const hint = (extension === "pdf")
                ? "The PDF may be corrupted, password-protected, or image-only (scanned)."
                : "The file may be corrupted or in an unexpected format.";
            return {
                success: false,
                error: `Failed to extract text from ${name}. ${hint}`,
            };
        }

        if (!extractedText || extractedText.trim().length === 0) {
            return {
                success: false,
                error: `No text found in "${name}". If this is a scanned PDF, it contains only images and cannot be processed.`,
            };
        }

        // 1. Insert into 'files' table
        let insertedFile;
        try {
            [insertedFile] = await db.insert(files).values({
                name: file.name,
                type: file.type || extension || "unknown",
                size: file.size,
                userId,
            }).returning();
        } catch (dbError: any) {
            console.error("Database insert error:", dbError);
            return { success: false, error: "Failed to save file record to database." };
        }

        // 2. Chunk text and generate embeddings
        let chunks;
        let embeddings;
        try {
            chunks = await chunkContent(extractedText);
            const chunkTexts = chunks.map(c => c.content);
            embeddings = await generateEmbeddings(chunkTexts);
        } catch (embeddingError: any) {
            console.error("Chunking/embedding error:", embeddingError);
            // Clean up the file record since embedding failed
            try {
                const { eq } = await import("drizzle-orm");
                await db.delete(files).where(eq(files.id, insertedFile.id));
            } catch { /* best effort cleanup */ }
            return {
                success: false,
                error: `Failed to generate embeddings for "${name}". The file may be too large or the API is rate-limited. Try again in a moment.`,
            };
        }

        // 3. Insert into 'documents' table linking to the file
        try {
            const records = chunks.map((chunk, index) => ({
                fileId: insertedFile.id,
                content: chunk.content,
                metadata: chunk.metadata,
                embeddings: embeddings[index],
            }));
            await db.insert(documents).values(records);
        } catch (docError: any) {
            console.error("Document insert error:", docError);
            return { success: false, error: "Failed to save document chunks to database." };
        }

        revalidatePath("/upload");

        return {
            success: true,
            message: `Created ${chunks.length} searchable chunks for ${file.name}`,
        }

    } catch (error: any) {
        console.error("Document processing error:", error);
        return {
            success: false,
            error: error.message || "An unexpected error occurred while processing the document.",
        }
    }
}
