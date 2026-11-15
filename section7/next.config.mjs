/** @type {import('next').NextConfig} */
const nextConfig = {
  // contracts/ts lives outside this app's root (shared across future frontend sections).
  experimental: {
    externalDir: true,
  },
  // The R1 preview screens became the real workspace in R2.
  async redirects() {
    return [
      { source: "/preview", destination: "/", permanent: false },
      { source: "/preview/:path*", destination: "/", permanent: false },
    ];
  },
};

export default nextConfig;
