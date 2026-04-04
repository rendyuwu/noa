export type TokenViolation = {
  filePath: string;
  line: number;
  utility: string;
};

export declare function findTokenViolations(source: string, filePath: string): TokenViolation[];

export declare function scanTokenViolations(rootDir?: string): Promise<TokenViolation[]>;

export declare function main(): Promise<void>;
