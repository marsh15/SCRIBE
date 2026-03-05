import type { NextConfig } from "next";
import path from "node:path";

const projectRoot = path.resolve(process.cwd());

const nextConfig: NextConfig = {
  serverExternalPackages: ["pdf-parse"],
  experimental: {
    proxyClientMaxBodySize: "120mb",
    serverActions: {
      bodySizeLimit: "120mb",
    },
  },
  turbopack: {
    root: projectRoot,
  },
  webpack: (config) => {
    config.context = projectRoot;
    config.resolve = config.resolve ?? {};
    config.resolve.modules = [
      path.join(projectRoot, "node_modules"),
      ...(Array.isArray(config.resolve.modules) ? config.resolve.modules : []),
    ];
    return config;
  },
};

export default nextConfig;
