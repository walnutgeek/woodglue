import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  base: "/console/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
