import type { DetailedHTMLProps, HTMLAttributes } from "react";

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "altcha-widget": DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & {
        auto?: "off" | "onfocus" | "onload" | "onsubmit";
        challenge?: string;
        language?: string;
        name?: string;
      };
    }
  }
}
