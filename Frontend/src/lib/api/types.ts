/**
 * Domain types for the VirusWatch surveillance backend (FastAPI).
 *
 * These mirror the expected response schemas of the backend API. They are
 * intentionally tolerant (`null`-friendly) because surveillance data is
 * frequently missing or delayed; the UI must never render a missing value
 * as zero.
 */

export type GeographicLevel = "city" | "province" | "national" | "other";

export type QualityStatus = "ok" | "stale" | "incomplete" | "unverified" | string;

export interface Disease {
  id: string;
  slug: string;
  name: string;
  category?: string | null;
  description?: string | null;
}

export interface Geography {
  id: string;
  name: string;
  /** What the data actually represents — never implied. */
  level: GeographicLevel | string;
  country?: string | null;
  parent_name?: string | null;
}

export interface Observation {
  id: string;
  disease_id?: string | null;
  disease_name?: string | null;
  geography_id?: string | null;
  geography_name?: string | null;
  geographic_level?: GeographicLevel | string | null;
  indicator?: string | null;
  value: number | null;
  unit?: string | null;
  denominator?: string | null;
  period_start?: string | null;
  period_end?: string | null;
  source_id?: string | null;
  source_name?: string | null;
  source_url?: string | null;
  source_publication_date?: string | null;
  retrieved_at?: string | null;
  quality_status?: QualityStatus | null;
}

export interface TimeSeriesPoint {
  /** Reporting period label, e.g. "2026-W43" or "2026-10". */
  period: string;
  period_start?: string | null | undefined;
  period_end?: string | null | undefined;
  value: number | null;
  unit?: string | null | undefined;
}

export interface Anomaly {
  id: string;
  disease_name?: string | null;
  geography_name?: string | null;
  geographic_level?: GeographicLevel | string | null;
  indicator?: string | null;
  period_start?: string | null;
  period_end?: string | null;
  observed_value?: number | null;
  baseline_value?: number | null;
  unit?: string | null;
  direction?: "increase" | "decrease" | string | null;
  magnitude?: string | number | null;
  detection_method?: string | null;
  threshold?: string | number | null;
  /** "validated" means statistically validated, not an outbreak confirmation. */
  status?: string | null;
  interpretation?: string | null;
  limitations?: string | null;
  source_quality?: QualityStatus | null;
  source_freshness?: string | null;
  created_at?: string | null;
  history?: TimeSeriesPoint[] | null;
}

export interface Report {
  id: string;
  title: string;
  summary?: string | null;
  organization?: string | null;
  publication_date?: string | null;
  geographic_scope?: string | null;
  reporting_period?: string | null;
  document_type?: string | null;
  source_url?: string | null;
  download_url?: string | null;
}

export interface Citation {
  title: string;
  organization?: string | null;
  publication_date?: string | null;
  reporting_period?: string | null;
  page?: number | string | null;
  url?: string | null;
  data_freshness?: string | null;
  limitations?: string | null;
}

export interface AiEvidence {
  citations: Citation[];
  limitations?: string[] | null;
}

export interface AiAnswer {
  answer: string;
  evidence?: AiEvidence | null;
  generated_at?: string | null;
  /** Analysis the backend itself computed and validated, if any. */
  validated_analysis?: string | null;
}

export interface NotificationItem {
  id: string;
  type?: string | null;
  disease?: string | null;
  geography?: string | null;
  evidence_summary?: string | null;
  citations?: Citation[] | null;
  created_at?: string | null;
  sent_at?: string | null;
  delivery_status?: string | null;
}

export interface DataSource {
  id: string;
  name: string;
  organization?: string | null;
  url?: string | null;
  supported_geographies?: string[] | null;
  supported_indicators?: string[] | null;
  last_successful_ingestion?: string | null;
  last_attempted_ingestion?: string | null;
  freshness_status?: string | null;
  ingestion_error?: string | null;
  record_count?: number | null;
}

export interface ActivityEvent {
  id: string;
  kind?: string | null;
  title: string;
  detail?: string | null;
  occurred_at?: string | null;
}

export interface SummaryMetrics {
  diseases_monitored?: number | null;
  regions_represented?: number | null;
  latest_observation_period?: string | null;
  validated_anomalies?: number | null;
  stale_or_incomplete_sources?: number | null;
}

export interface DataFreshness {
  last_successful_retrieval?: string | null;
  last_source_publication_date?: string | null;
  current_reporting_period?: string | null;
  freshness_status?: string | null;
  missing_or_delayed_sources?: string[] | null;
  /** The cadence of the underlying data, e.g. "weekly". Never "real-time". */
  reporting_cadence?: string | null;
}

export interface Paginated<T> {
  items: T[];
  total?: number | null;
  page?: number | null;
  page_size?: number | null;
}
