import type { MetadataRoute } from "next";

const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? "https://scribe-marsh.vercel.app";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: ["/", "/pricing", "/changelog"],
      disallow: ["/api/", "/admin/", "/chat/", "/files/", "/settings/", "/upload/"],
    },
    sitemap: `${baseUrl}/sitemap.xml`,
  };
}
