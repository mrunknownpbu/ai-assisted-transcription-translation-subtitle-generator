import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import "./styles/global.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Push updates (useEventStream) drive refetch on real changes;
      // this is just a slow safety-net fallback, not the primary
      // update mechanism the old app relied on (setInterval(loadJobs, 2000)).
      refetchInterval: 30_000,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
