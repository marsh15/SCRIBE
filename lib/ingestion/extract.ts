import pdf from "pdf-parse";
import { parse as csvParse } from "csv-parse/sync";
import * as mammoth from "mammoth";

export interface ExtractedContent {
  extractedText: string;
  numPages?: number;
}

export async function extractTextFromBuffer(input: {
  buffer: Buffer;
  fileName: string;
  mimeType: string;
}): Promise<ExtractedContent> {
  const { buffer, fileName, mimeType } = input;
  const extension = fileName.split(".").pop()?.toLowerCase();
  let extractedText = "";
  let numPages: number | undefined;

  if (mimeType === "application/pdf" || extension === "pdf") {
    const data = await pdf(buffer);
    extractedText = data.text;
    numPages = data.numpages;
  } else if (
    mimeType === "text/plain" ||
    mimeType === "text/markdown" ||
    extension === "txt" ||
    extension === "md"
  ) {
    extractedText = buffer.toString("utf-8");
  } else if (mimeType === "text/csv" || extension === "csv") {
    const records = csvParse(buffer.toString("utf-8"), {
      skip_empty_lines: true,
    }) as unknown[][];
    extractedText = records.map((row) => row.join(" ")).join("\n");
  } else if (
    mimeType === "application/vnd.openxmlformats-officedocument.wordprocessingml.document" ||
    extension === "docx"
  ) {
    const data = await mammoth.extractRawText({ buffer });
    extractedText = data.value;
  } else {
    throw new Error(`Unsupported file type: .${extension || mimeType}`);
  }

  if (!extractedText || extractedText.trim().length === 0) {
    throw new Error(
      `No text extracted from ${fileName}. Scanned PDFs and image-only documents are not supported yet.`
    );
  }

  return { extractedText, numPages };
}
