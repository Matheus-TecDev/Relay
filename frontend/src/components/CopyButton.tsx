import { Check, Copy } from "lucide-react";
import { useState } from "react";

type CopyButtonProps = {
  value: string;
  label: string;
};

export function CopyButton({ value, label }: CopyButtonProps) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  async function copyValue() {
    try {
      await navigator.clipboard.writeText(value);
      setState("copied");
      window.setTimeout(() => setState("idle"), 1400);
    } catch {
      setState("failed");
      window.setTimeout(() => setState("idle"), 1800);
    }
  }

  return (
    <button className="copy-button" type="button" onClick={copyValue} title={`Copy ${label}`}>
      {state === "copied" ? <Check size={14} /> : <Copy size={14} />}
      <span>{state === "failed" ? "Failed" : label}</span>
    </button>
  );
}
