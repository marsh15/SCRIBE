"use server";

import { getUserId } from "@/lib/auth";
import { canViewEval } from "@/lib/eval/queries";

/** Whether the signed-in user may open /eval (drives the sidebar link). Signed out or any error: false. */
export async function getEvalAccess(): Promise<boolean> {
  try {
    return await canViewEval(await getUserId());
  } catch {
    return false;
  }
}
