export type PendingAttachment = {
  id: string;
  name: string;
  content: string;
  sizeBytes: number;
  mediaType: string;
};

export type SentAttachment = Pick<
  PendingAttachment,
  "name" | "sizeBytes" | "mediaType"
>;

export const MAX_FILES = 3;
export const MAX_TEXT_BYTES = 32_768;
export const MAX_TEXT_TOTAL_BYTES = 65_536;
export const MAX_BINARY_BYTES = 2_097_152;
export const MAX_TOTAL_BYTES = 4_194_304;
export const ACCEPTED_EXTENSIONS = [
  ".txt",
  ".md",
  ".csv",
  ".tsv",
  ".json",
  ".sql",
  ".yaml",
  ".yml",
  ".xml",
  ".log",
  ".py",
  ".js",
  ".ts",
  ".tsx",
  ".jsx",
  ".html",
  ".css",
  ".sh",
  ".toml",
  ".pdf",
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".gif",
];

function mediaType(name: string): string {
  const lower = name.toLowerCase();
  if (lower.endsWith(".pdf")) return "application/pdf";
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".gif")) return "image/gif";
  if (ACCEPTED_EXTENSIONS.some((extension) => lower.endsWith(extension))) {
    return "text/plain";
  }
  throw new Error(`${name}: unsupported file type.`);
}

function base64Data(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () =>
      reject(new Error(`${file.name}: file could not be read.`));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string" || !result.includes(",")) {
        reject(new Error(`${file.name}: file could not be read.`));
        return;
      }
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}

export async function readAttachment(file: File): Promise<PendingAttachment> {
  if (
    !file.name ||
    file.name.length > 255 ||
    /[\\/]/.test(file.name) ||
    Array.from(file.name).some((character) => character.charCodeAt(0) < 32)
  ) {
    throw new Error("Invalid file name.");
  }
  const type = mediaType(file.name);
  const limit = type === "text/plain" ? MAX_TEXT_BYTES : MAX_BINARY_BYTES;
  if (!file.size || file.size > limit) {
    throw new Error(
      `${file.name}: file must be ${type === "text/plain" ? "32 KB" : "2 MB"} or smaller.`,
    );
  }
  let content: string;
  if (type === "text/plain") {
    try {
      content = new TextDecoder("utf-8", { fatal: true }).decode(
        await file.arrayBuffer(),
      );
    } catch {
      throw new Error(`${file.name}: file must be UTF-8 text.`);
    }
    if (!content || content.includes("\0")) {
      throw new Error(`${file.name}: file must contain UTF-8 text.`);
    }
  } else {
    content = await base64Data(file);
  }
  return {
    id: crypto.randomUUID(),
    name: file.name,
    content,
    sizeBytes: file.size,
    mediaType: type,
  };
}
