import { useEffect, useRef } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { fetchXtermState } from "./api";
import "@xterm/xterm/css/xterm.css";

const THEME = {
  background: "#252525",
  foreground: "#D4D4D4",
  cursor: "#D4D4D4",
  selectionBackground: "#264F78",
  black: "#000000",
  red: "#CD3131",
  green: "#0DBC79",
  yellow: "#E5E510",
  blue: "#2472C8",
  magenta: "#BC3FBC",
  cyan: "#11A8CD",
  white: "#E5E5E5",
  brightBlack: "#666666",
  brightRed: "#F14C4C",
  brightGreen: "#23D18B",
  brightYellow: "#F5F543",
  brightBlue: "#3B8EEA",
  brightMagenta: "#D670D6",
  brightCyan: "#29B8DB",
  brightWhite: "#E5E5E5",
};

interface TerminalProps {
  sessionId: string;
  cellId: string;
  alive: boolean;
  onResume: () => Promise<void>;
}

export default function Terminal({ sessionId, cellId, alive, onResume }: TerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const resumingRef = useRef(false);

  useEffect(() => {
    if (!containerRef.current) return;

    const term = new XTerm({
      cursorBlink: false,
      fontSize: 13,
      fontFamily: "Menlo, Monaco, 'Courier New', monospace",
      scrollback: 20000,
      theme: THEME,
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(containerRef.current);
    fitAddon.fit();
    termRef.current = term;

    const container = containerRef.current;

    // Scroll handling: prevent page scroll while interacting with the terminal.
    // Only allow passthrough when at a scroll boundary AND idle for COOLDOWN ms.
    let lastScrollTime = 0;
    const COOLDOWN = 500;
    const onWheel = (e: WheelEvent) => {
      const now = Date.now();
      const buf = term.buffer.active;
      const atTop = buf.viewportY === 0;
      const atBottom = buf.viewportY === buf.baseY;
      const scrollingDown = e.deltaY > 0;
      const scrollingUp = e.deltaY < 0;
      const atBoundary = (scrollingDown && atBottom) || (scrollingUp && atTop);

      if (atBoundary) {
        if (now - lastScrollTime > COOLDOWN) {
          return; // cooldown expired, let page scroll
        }
        e.preventDefault(); // within cooldown, block page scroll but don't reset timer
        return;
      }

      // non-boundary scroll: let xterm handle it (it calls preventDefault itself)
      lastScrollTime = now;
    };
    container.addEventListener("wheel", onWheel, { capture: true, passive: false });

    const resizeObserver = new ResizeObserver(() => {
      fitAddon.fit();
    });
    resizeObserver.observe(container);

    let cleanup: () => void;

    if (alive) {
      // --- LIVE MODE ---
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(
        `${protocol}//${window.location.host}/ws/sessions/${sessionId}`
      );
      ws.binaryType = "arraybuffer";

      ws.onopen = () => {
        fitAddon.fit();
        ws.send(
          JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols })
        );
      };

      ws.onmessage = (event) => {
        term.write(new Uint8Array(event.data));
      };

      ws.onclose = () => {
        term.write("\r\n\x1b[90m[session ended]\x1b[0m\r\n");
      };

      term.attachCustomKeyEventHandler(() => true);

      term.onData((data) => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "input", data }));
        }
      });

      // Send resize events to the PTY
      const resizeObserver2 = new ResizeObserver(() => {
        fitAddon.fit();
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(
            JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols })
          );
        }
      });
      resizeObserver2.observe(container);

      cleanup = () => {
        resizeObserver2.disconnect();
        ws.close();
      };
    } else {
      // --- REPLAY MODE ---
      fetchXtermState(cellId, sessionId)
        .then((buffer) => term.write(new Uint8Array(buffer)))
        .catch(() => {});

      const doResume = async () => {
        if (resumingRef.current) return;
        resumingRef.current = true;
        disposable.dispose();
        container.removeEventListener("mousedown", doResume);
        term.write("\r\n\x1b[93m[resuming session...]\x1b[0m\r\n");
        await onResume();
      };

      const disposable = term.onData(doResume);
      container.addEventListener("mousedown", doResume);

      cleanup = () => {
        disposable.dispose();
        container.removeEventListener("mousedown", doResume);
      };
    }

    return () => {
      container.removeEventListener("wheel", onWheel, { capture: true });
      resizeObserver.disconnect();
      cleanup();
      term.dispose();
    };
  }, [sessionId, alive]);

  return (
    <div
      ref={containerRef}
      style={{ height: "400px", width: "100%", padding: "10px", background: "#252525", boxSizing: "border-box" }}
      className="rounded overflow-hidden"
    />
  );
}
