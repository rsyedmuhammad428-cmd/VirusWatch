import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

/**
 * Global disease and geography filters, preserved across navigation while
 * the user moves between related pages.
 */

export interface GlobalFilters {
  disease: string;
  geography: string;
}

const DEFAULT_FILTERS: GlobalFilters = { disease: "", geography: "" };
const STORAGE_KEY = "viruswatch.filters";

interface FiltersContextValue {
  filters: GlobalFilters;
  setFilter: (key: keyof GlobalFilters, value: string) => void;
  reset: () => void;
}

const FiltersContext = createContext<FiltersContextValue | null>(null);

export function GlobalFiltersProvider({ children }: { children: ReactNode }) {
  const [filters, setFilters] = useState<GlobalFilters>(DEFAULT_FILTERS);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as Partial<GlobalFilters>;
        setFilters({ ...DEFAULT_FILTERS, ...parsed });
      }
    } catch {
      // Ignore malformed stored filters.
    }
  }, []);

  const value = useMemo<FiltersContextValue>(
    () => ({
      filters,
      setFilter: (key, val) =>
        setFilters((prev) => {
          const next = { ...prev, [key]: val };
          try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
          } catch {
            // Non-fatal.
          }
          return next;
        }),
      reset: () => {
        setFilters(DEFAULT_FILTERS);
        try {
          window.localStorage.removeItem(STORAGE_KEY);
        } catch {
          // Non-fatal.
        }
      },
    }),
    [filters],
  );

  return <FiltersContext.Provider value={value}>{children}</FiltersContext.Provider>;
}

export function useGlobalFilters(): FiltersContextValue {
  const ctx = useContext(FiltersContext);
  if (!ctx) throw new Error("useGlobalFilters must be used inside GlobalFiltersProvider");
  return ctx;
}
