export function canOpenOriginalPdfFile(input: {
  isPdf: boolean;
  hasFileData: boolean;
  hasStorageUrl: boolean;
}) {
  return input.isPdf && (input.hasFileData || input.hasStorageUrl);
}

export function getMissingOriginalFileMessage(fileName: string) {
  return `The original ${fileName} binary is not stored for this browser-processed upload. You can still review the extracted text and indexed chunks below.`;
}
