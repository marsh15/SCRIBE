import { put } from "@vercel/blob";

function blobToken() {
  const token = process.env.BLOB_READ_WRITE_TOKEN;
  return token ?? null;
}

export async function uploadBufferToBlob(input: {
  pathname: string;
  contentType: string;
  body: ArrayBuffer | Uint8Array;
}) {
  const token = blobToken();
  if (!token) {
    throw new Error("Missing BLOB_READ_WRITE_TOKEN");
  }

  const bodyBuffer =
    input.body instanceof ArrayBuffer
      ? Buffer.from(new Uint8Array(input.body))
      : Buffer.from(input.body);

  const blob = await put(input.pathname, bodyBuffer, {
    access: "private",
    contentType: input.contentType,
    addRandomSuffix: true,
    token,
  });

  return {
    url: String(blob.url),
    pathname: String(blob.pathname),
    size: Number((blob as any).size ?? bodyBuffer.byteLength),
  };
}

export async function downloadBlobToBuffer(url: string) {
  const token = blobToken();
  const res = await fetch(url, {
    method: "GET",
    headers: token
      ? {
        Authorization: `Bearer ${token}`,
      }
      : undefined,
  });

  if (!res.ok) {
    throw new Error(`Failed to fetch blob (${res.status})`);
  }

  return res.arrayBuffer();
}
