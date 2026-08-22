/** @type {import('next').NextConfig} */
const nextConfig = {
  // contracts/ts lives outside this app's root (shared across future frontend sections).
  experimental: {
    externalDir: true,
  },
};

export default nextConfig;
