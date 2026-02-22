"use server";

import pdf from "pdf-parse";
import { parse as csvParse } from "csv-parse/sync";
import * as mammoth from "mammoth";
import { db } from "@/lib/db-config";
import { files, documents } from "@/lib/db-schema";
import { generateEmbeddings } from "@/lib/embeddings";
import { chunkContent } from "@/lib/chunking";
import { revalidatePath } from "next/cache";

export async function processDocument(formData: FormData) {
    try {
        const file = formData.get("file") as File;
        if (!file) {
            return { success: false, error: "No file uploaded" };
        }

        const bytes = await file.arrayBuffer();
        const buffer = Buffer.from(bytes);
        let extractedText = "";

        // Extract text based on file type
        const type = file.type;
        const name = file.name;
        const extension = name.split('.').pop()?.toLowerCase();

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
            return { success: false, error: "Unsupported file type" };
        }

        if (!extractedText || extractedText.trim().length === 0) {
            return {
                success: false,
                error: "No text found in file",
            };
        }

        // 1. Insert into 'files' table
        const [insertedFile] = await db.insert(files).values({
            name: file.name,
            type: file.type || extension || "unknown",
            size: file.size,
        }).returning();

        // 2. Chunk text and generate embeddings
        const chunks = await chunkContent(extractedText);
        const chunkTexts = chunks.map(c => c.content);
        const embeddings = await generateEmbeddings(chunkTexts);

        // 3. Insert into 'documents' table linking to the file
        const records = chunks.map((chunk, index) => ({
            fileId: insertedFile.id,
            content: chunk.content,
            metadata: chunk.metadata,
            embeddings: embeddings[index],
        }));

        await db.insert(documents).values(records);
        revalidatePath("/upload"); // Revalidate cache

        return {
            success: true,
            message: `Created ${records.length} searchable chunks for ${file.name}`,
        }

    } catch (error) {
        console.error("Document processing error", error);
        return {
            success: false,
            error: "Failed to process document",
        }
    }
}
