import { z } from "zod";
const optionalSecret = z.string().trim().min(1).optional();
const schema = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
  NEON_DATABASE_URL: optionalSecret,
  GOOGLE_GENERATIVE_AI_API_KEY: optionalSecret,
  BLOB_READ_WRITE_TOKEN: optionalSecret,
  UPLOAD_SIGNING_SECRET: optionalSecret,
  CRON_SECRET: optionalSecret,
  RAZORPAY_KEY_ID: optionalSecret,
  RAZORPAY_KEY_SECRET: optionalSecret,
  RAZORPAY_WEBHOOK_SECRET: optionalSecret,
});
export const env = schema.parse(process.env);
export function requireEnv<K extends keyof typeof env>(key: K): NonNullable<(typeof env)[K]> {
  const value = env[key];
  if (!value) throw new Error(`Missing required server configuration: ${key}`);
  return value as NonNullable<(typeof env)[K]>;
}
