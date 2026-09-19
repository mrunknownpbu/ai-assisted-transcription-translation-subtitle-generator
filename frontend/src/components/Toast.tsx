import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

interface ToastMessage {
  id: number;
  text: string;
  kind: "ok" | "error";
}

interface ToastContextValue {
  notify: (text: string, kind?: "ok" | "error") => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

let nextId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<ToastMessage[]>([]);

  const notify = useCallback((text: string, kind: "ok" | "error" = "ok") => {
    const id = nextId++;
    setMessages((prev) => [...prev, { id, text, kind }]);
    setTimeout(() => setMessages((prev) => prev.filter((m) => m.id !== id)), 5000);
  }, []);

  return (
    <ToastContext.Provider value={{ notify }}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {messages.map((m) => (
          <div key={m.id} className={`toast toast-${m.kind}`}>
            {m.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast() must be used inside <ToastProvider>");
  return ctx;
}
