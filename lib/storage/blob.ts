const DEFAULT_BLOB_BASE = "https://blob.vercel-storage.com";

function blobToken() {
  const token = process.env.BLOB_READ_WRITE_TOKEN;
  return token ?? null;
}

function blobBase() {
  return process.env.BLOB_API_BASE_URL ?? DEFAULT_BLOB_BASE;
}

function normalizePathname(pathname: string) {
  return pathname
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
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

  const url = `${blobBase()}/${normalizePathname(input.pathname)}`;

  const bodyBuffer =
    input.body instanceof ArrayBuffer
      ? Buffer.from(new Uint8Array(input.body))
      : Buffer.from(input.body);

  const res = await fetch(url, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${token}`,
      "x-content-type": input.contentType,
      "x-add-random-suffix": "1",
      "x-cache-control-max-age": "31536000",
    },
    body: bodyBuffer,
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Blob upload failed (${res.status}): ${text}`);
  }

  let json: Record<string, unknown> = {};
  try {
    json = (await res.json()) as Record<string, unknown>;
  } catch {
    json = {};
  }

  return {
    url: String(json.url ?? url),
    pathname: String(json.pathname ?? input.pathname),
    size: Number(json.size ?? bodyBuffer.byteLength),
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
