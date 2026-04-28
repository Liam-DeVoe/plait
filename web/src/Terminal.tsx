import { useEffect, useRef, useState, useCallback } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

const STORAGE_KEY = "plait:terminal-height";
const DEFAULT_HEIGHT = 400;
const MIN_HEIGHT = 150;
const MAX_HEIGHT = 1200;

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
  alive: boolean;
  autoFocus?: boolean;
  onResume: () => Promise<void>;
  fetchXtermState: () => Promise<ArrayBuffer>;
}

export default function Terminal({ sessionId, alive, autoFocus, onResume, fetchXtermState }: TerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const resumingRef = useRef(false);
  const [height, setHeight] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, parseInt(saved, 10) || DEFAULT_HEIGHT)) : DEFAULT_HEIGHT;
  });

  const onResizeHandleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startHeight = height;
    document.body.style.cursor = "ns-resize";
    document.body.style.userSelect = "none";

    const onMouseMove = (ev: MouseEvent) => {
      setHeight(Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, startHeight + (ev.clientY - startY))));
    };

    const onMouseUp = (ev: MouseEvent) => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      const finalHeight = Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, startHeight + (ev.clientY - startY)));
      localStorage.setItem(STORAGE_KEY, String(finalHeight));
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  }, [height]);

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
    //
    // Goal: if you scroll inside the terminal and hit a boundary, the entire
    // remainder of that scroll gesture (including momentum/inertia) is absorbed
    // — it never leaks through to scroll the page. But if you're already at a
    // boundary and start a *fresh* scroll gesture, it passes through to the page.
    //
    // To distinguish "momentum from a gesture that moved the viewport" from
    // "a fresh gesture at the boundary," we track two timestamps:
    //   - lastViewportMoveTime: last wheel event that actually scrolled the
    //     xterm viewport (i.e., a non-boundary scroll).
    //   - lastWheelTime: last wheel event of any kind that was NOT a passthrough.
    //
    // A boundary scroll passes through to the page only when BOTH cooldowns
    // have expired — the viewport hasn't moved recently AND there have been no
    // wheel events (blocked or otherwise) recently. The second condition is what
    // prevents momentum from leaking: blocked boundary events keep resetting
    // lastWheelTime, so even after the viewport-move cooldown expires, passthrough
    // is suppressed until the momentum fully stops.
    let lastViewportMoveTime = 0;
    let lastWheelTime = 0;
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
        const freshGesture =
          now - lastViewportMoveTime > COOLDOWN &&
          now - lastWheelTime > COOLDOWN;
        if (freshGesture) {
          return; // fresh scroll at boundary — let page scroll
        }
        e.preventDefault();
        lastWheelTime = now; // keep blocking until momentum fully stops
        return;
      }

      // non-boundary scroll: let xterm handle it (it calls preventDefault itself)
      lastViewportMoveTime = now;
      lastWheelTime = now;
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
        if (autoFocus) {
          container.scrollIntoView({ behavior: "smooth", block: "center" });
          term.focus();
        }
      };

      ws.onmessage = (event) => {
        term.write(new Uint8Array(event.data));
      };

      ws.onclose = () => {
        term.write("\r\n\x1b[90m[session ended]\x1b[0m\r\n");
      };

      term.attachCustomKeyEventHandler((e) => {
        if (e.type !== "keydown") return true;
        const send = (data: string) => {
          e.preventDefault();
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "input", data }));
          }
          return false;
        };
        // Cmd+Backspace -> kill line (Ctrl+U)
        if (e.metaKey && e.key === "Backspace") return send("\x15");
        // Cmd+Left -> beginning of line (Ctrl+A)
        if (e.metaKey && e.key === "ArrowLeft") return send("\x01");
        // Cmd+Right -> end of line (Ctrl+E)
        if (e.metaKey && e.key === "ArrowRight") return send("\x05");
        // Option+Left -> word back (ESC b)
        if (e.altKey && e.key === "ArrowLeft") return send("\x1bb");
        // Option+Right -> word forward (ESC f)
        if (e.altKey && e.key === "ArrowRight") return send("\x1bf");
        // Shift+Enter -> newline without submit (bracketed paste)
        if (e.shiftKey && e.key === "Enter") return send("\x1b[200~\n\x1b[201~");
        return true;
      });

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
      fetchXtermState()
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

      // Delay attaching resume listeners so replayed xterm state
      // (which may re-enable focus reporting) doesn't immediately
      // trigger a resume via onData or mousedown.
      let disposable = { dispose: () => {} };
      const attachTimer = setTimeout(() => {
        disposable = term.onData(doResume);
        container.addEventListener("mousedown", doResume);
      }, 500);

      cleanup = () => {
        clearTimeout(attachTimer);
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
  }, [sessionId, alive, autoFocus]);

  return (
    <>
      <div
        ref={containerRef}
        style={{ height: `${height}px`, width: "100%", padding: "10px", background: "#252525", boxSizing: "border-box" }}
        className="terminal__container"
      />
      <div className="terminal__resize-handle" onMouseDown={onResizeHandleMouseDown} />
    </>
  );
}
