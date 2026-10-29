import tseslint from "typescript-eslint";

export default [
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { "@typescript-eslint": tseslint.plugin },
    rules: { "@typescript-eslint/no-unnecessary-condition": "error" },
  },
];
