import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` proxies /api to a local backend
// (uvicorn next.api.main:app --port 8000). Prod: FastAPI serves dist/.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
