export type ThemeTokens = {
  light: Set<string>;
  dark: Set<string>;
};

export declare function collectThemeTokens(source: string): ThemeTokens;

export declare function findMissingThemeTokens(source: string): string[];

export declare function findExtraThemeTokens(source: string): string[];

export declare function main(): Promise<void>;
