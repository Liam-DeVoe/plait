import { useEffect, useRef } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

export default function Terminal({ sessionId }: { sessionId: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const term = new XTerm({
      cursorBlink: false,
      fontSize: 13,
      fontFamily: "Menlo, Monaco, 'Courier New', monospace",
      scrollback: 20000,
      theme: {
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
      },
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(containerRef.current);
    fitAddon.fit();
    termRef.current = term;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${protocol}//${window.location.host}/ws/sessions/${sessionId}`
    );
    ws.binaryType = "arraybuffer";

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

    const resizeObserver = new ResizeObserver(() => {
      fitAddon.fit();
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols })
        );
      }
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      ws.close();
      term.dispose();
    };
  }, [sessionId]);

  return (
    <div
      ref={containerRef}
      style={{ height: "400px", width: "100%" }}
      className="rounded overflow-hidden"
    />
  );
}
