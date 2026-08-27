import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { App } from "./app";
import { SiteConfigProvider } from "./site-config";

createRoot(document.getElementById("root")!).render(
  <StrictMode><SiteConfigProvider><App /></SiteConfigProvider></StrictMode>,
);
