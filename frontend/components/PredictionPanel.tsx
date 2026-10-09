"use client";

import { useState } from "react";

import { ProbabilityBar } from "@/components/charts";
import { PUBLIC_API_URL, type Prediction } from "@/lib/api";
import { fmtArs } from "@/lib/format";

export interface PredictableModel {
  name: string;
  algorithm: string;
  horizon: string;
}

const HORIZONS = [
  { key: "1d", label: "1 day" },
  { key: "1w", label: "1 week" },
  { key: "1m", label: "1 month" },
  { key: "3m", label: "3 months" },
  { key: "6m", label: "6 months" },
  { key: "1y", label: "1 year" },
  { key: "2y", label: "2 years" },
] as const;

/**
 * Direction probabilities for one CEDEAR over a chosen horizon.
 *
 * The horizon and the model are chosen by the reader; the backend serves the
 * registered (name, horizon) artifact and refuses anything else. This
 * component never computes, defaults or reshapes anything: backend refusals
 * (unknown horizon, untrained model, mismatched features) are shown verbatim
 * instead of being papered over.
 */
export function PredictionPanel({
  symbol,
  models,
}: {
  symbol: string;
  models: PredictableModel[];
}) {
  const [horizon, setHorizon] = useState<string>("1d");
  const [selected, setSelected] = useState(models[0]?.name ?? "");
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const available = models.filter((model) => model.horizon === horizon);

  if (models.length === 0) {
    return (
      <p className="text-sm text-slate-500">
        No registered models. Train and register one before predictions can be served; this
        panel shows nothing rather than a default.
      </p>
    );
  }

  async function predict(modelName: string, chosenHorizon: string) {
    setSelected(modelName);
    setLoading(true);
    setError(null);
    setPrediction(null);
    try {
      const params = new URLSearchParams({ model: modelName, horizon: chosenHorizon });
      const response = await fetch(
        `${PUBLIC_API_URL}/api/v1/cedears/${encodeURIComponent(symbol)}/prediction?${params.toString()}`,
        { headers: { accept: "application/json" } },
      );
      const body = (await response.json()) as
        | Prediction
        | { detail: { error: string; message?: string } | string };
      if (!response.ok) {
        const detail = (body as { detail?: { message?: string; error?: string } }).detail;
        setError(
          typeof detail === "string"
            ? detail
            : (detail?.message ?? detail?.error ?? `HTTP ${response.status}`),
        );
        return;
      }
      setPrediction(body as Prediction);
    } catch (err) {
      setError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }

  function chooseHorizon(next: string) {
    setHorizon(next);
    setPrediction(null);
    setError(null);
    const first = models.find((model) => model.horizon === next);
    setSelected(first?.name ?? "");
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Forecast horizon">
        {HORIZONS.map((option) => (
          <button
            key={option.key}
            type="button"
            onClick={() => chooseHorizon(option.key)}
            aria-pressed={horizon === option.key}
            className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ${
              horizon === option.key
                ? "bg-sky-600 text-white ring-sky-600"
                : "text-slate-600 ring-slate-300 hover:bg-slate-100 dark:text-slate-300 dark:ring-slate-700 dark:hover:bg-slate-800"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      {available.length === 0 ? (
        <p className="text-sm text-slate-500">
          No registered model serves this horizon for {symbol} yet.
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="model-select" className="text-sm text-slate-500">
            Model
          </label>
          <select
            id="model-select"
            value={selected}
            onChange={(event) => void predict(event.target.value, horizon)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-mono text-sm dark:border-slate-700 dark:bg-slate-900"
          >
            {available.map((model) => (
              <option key={model.name} value={model.name}>
                {model.name} ({model.algorithm})
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => selected && void predict(selected, horizon)}
            disabled={loading || !selected}
            className="rounded-lg bg-sky-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
          >
            {loading ? "Predicting…" : "Predict"}
          </button>
        </div>
      )}

      {error ? (
        <p className="text-sm text-amber-700 dark:text-amber-300">
          The backend refused: {error}
        </p>
      ) : null}

      {prediction ? (
        <div className="space-y-3">
          <p className="text-xs text-slate-500">{prediction.horizon_detail}</p>
          <ProbabilityBar up={prediction.probability_up} />
          <dl className="space-y-1.5 text-sm">
            <ProvRow label="Horizon" value={prediction.horizon} />
            <ProvRow
              label="Model"
              value={`${prediction.model.name} · ${prediction.model.algorithm}`}
            />
            <ProvRow
              label="Feature version"
              value={`${prediction.feature_version}${prediction.feature_version_match ? "" : ` (trained on ${prediction.model.feature_version})`}`}
            />
            <ProvRow label="Predicted at" value={prediction.as_of} />
            <ProvRow label="Latest stored close" value={fmtArs(prediction.actual_close)} />
            <ProvRow
              label="Ratio in force"
              value={prediction.current_ratio_formatted ?? "unknown"}
            />
          </dl>
          {!prediction.feature_version_match ? (
            <p className="text-xs text-amber-700 dark:text-amber-300">
              Warning: live features differ from the training version. Treat this probability
              with extra scepticism.
            </p>
          ) : null}
        </div>
      ) : (
        !error &&
        available.length > 0 && (
          <p className="text-sm text-slate-500">
            Choose a horizon and a model, then press Predict. The backend computes live
            features from stored bars and answers with a probability - never a certainty.
          </p>
        )
      )}
    </div>
  );
}

function ProvRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="text-right font-mono text-xs text-slate-800 dark:text-slate-200">{value}</dd>
    </div>
  );
}
