import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ThemeProvider } from "./components/ui/ThemeProvider";
import { ToastProvider } from "./components/ui/Toast";
import { ConfirmProvider } from "./components/ui/hooks/useConfirm";
import "./styles/tokens.css";
import "./styles/primitives.css";
import "./styles/base.css";
import "./styles/_helpers.css";
import "./styles/_modal.css";
import "./styles/_empty.css";
import "./styles/_toast.css";
import "./styles/_collector-reader.css";
import "./styles/_signal.css";
import { installSpotlight } from "./lib/spotlight";

installSpotlight();

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <ThemeProvider>
      <ToastProvider>
        <ConfirmProvider>
          <App />
        </ConfirmProvider>
      </ToastProvider>
    </ThemeProvider>
  </StrictMode>
);
