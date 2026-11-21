import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://bocchisad.github.io",
  base: "/TestPilot",
  trailingSlash: "always",
  i18n: {
    defaultLocale: "en",
    locales: ["en", "ru"],
    routing: {
      prefixDefaultLocale: false,
    },
  },
});
