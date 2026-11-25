// TS 6+ type-checks side-effect imports (import "./globals.css"); declare CSS so tsc passes on old and new TypeScript.
declare module "*.css";
