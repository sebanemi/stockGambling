import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

/**
 * ESLint flat configuration.
 *
 * `eslint-config-next` v16 ships native flat configs, so no `FlatCompat`
 * bridge (and no `@eslint/eslintrc`) is required.
 *
 * `core-web-vitals` is included because the dashboard will render price
 * charts and prediction gauges.
 */
const eslintConfig = [
  {
    ignores: [
      "node_modules/**",
      ".next/**",
      "out/**",
      "build/**",
      "next-env.d.ts",
    ],
  },
  ...nextCoreWebVitals,
  ...nextTypeScript,
  {
    rules: {
      // The dashboard formats prices and probabilities for readability; that
      // is an intentional departure from the default numeric formatting rules.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
];

export default eslintConfig;
