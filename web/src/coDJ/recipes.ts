// The recipe library: every contracts/recipes/*.json, checked against the contract at load.
import { assertRecipe, type Recipe } from "../contracts/recipe";

const files = import.meta.glob("../../../contracts/recipes/*.json", { eager: true, import: "default" });

export const RECIPES: readonly Recipe[] = Object.values(files)
  .map((r) => { assertRecipe(r); return r; })
  .sort((a, b) => a.name.localeCompare(b.name));
