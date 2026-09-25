import { Link } from "react-router-dom";

import { useSeriesList } from "../api/hooks";
import type { SeriesSummary } from "../api/types";

function SeriesCardBody({ s }: { s: SeriesSummary }) {
  return (
    <div className="panel">
      <h3>{s.title ?? (s.tvdb_id === null ? "Ungrouped" : `Series #${s.tvdb_id}`)}</h3>
      <div className="series-counts">
        <span>
          {s.episodes} episode{s.episodes === 1 ? "" : "s"}
        </span>
        {s.total !== s.episodes && (
          <span>
            {s.total} job{s.total === 1 ? "" : "s"}
          </span>
        )}
        {Object.entries(s.counts)
          .filter(([, n]) => n > 0)
          .map(([status, n]) => (
            <span key={status}>
              {n} {status.toLowerCase()}
            </span>
          ))}
      </div>
    </div>
  );
}

export function SeriesListPage() {
  const { data, isLoading } = useSeriesList();

  if (isLoading) return <div>Loading&hellip;</div>;

  return (
    <section>
      <div className="panel-head">
        <h2>Series</h2>
      </div>
      <div className="card-grid">
        {data?.series.map((s) =>
          s.tvdb_id === null ? (
            <div key="ungrouped" className="series-card">
              <SeriesCardBody s={s} />
            </div>
          ) : (
            <Link key={s.tvdb_id} className="series-card" to={`/series/${s.tvdb_id}`}>
              <SeriesCardBody s={s} />
            </Link>
          ),
        )}
      </div>
    </section>
  );
}
