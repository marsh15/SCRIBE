import { NextResponse } from "next/server";
import { z } from "zod";
import { getUserId } from "@/lib/auth";
import { createUploadToken } from "@/lib/uploads/signature";
import { getUserMaxUploadBytes } from "@/lib/uploads/limits";
import { getUsageSummary } from "@/lib/billing/usage";

const schema = z.object({
  fileName: z.string().min(1),
  fileSize: z.number().int().positive(),
  fileType: z.string().min(1),
});

export async function POST(req: Request) {
  try {
    const userId = await getUserId();
    const usageSummary = await getUsageSummary(userId);
    if (!usageSummary.allowOverage && usageSummary.projectedOverageInr > 0) {
      return NextResponse.json(
        { error: "Free plan storage/usage limit reached. Upgrade to upload more files." },
        { status: 402 }
      );
    }
    const body = schema.safeParse(await req.json());

    if (!body.success) {
      return NextResponse.json({ error: body.error.flatten() }, { status: 400 });
    }

    const { maxBytes, maxMb, planCode } = await getUserMaxUploadBytes(userId);

    if (body.data.fileSize > maxBytes) {
      return NextResponse.json(
        {
          error: `File exceeds plan limit (${maxMb} MB for ${planCode.toUpperCase()} plan).`,
          maxBytes,
          maxMb,
          planCode,
        },
        { status: 413 }
      );
    }

    const expiresAt = Date.now() + 10 * 60 * 1000;
    const token = createUploadToken({
      userId,
      fileName: body.data.fileName,
      fileSize: body.data.fileSize,
      fileType: body.data.fileType,
      expiresAt,
    });

    return NextResponse.json({
      ok: true,
      uploadToken: token,
      expiresAt,
      maxBytes,
      maxMb,
      planCode,
      uploadUrl: "/api/uploads/complete",
      method: "POST",
    });
  } catch (error) {
    console.error("Upload sign error:", error);
    return NextResponse.json({ error: "Failed to prepare upload" }, { status: 500 });
  }
}
