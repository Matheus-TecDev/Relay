type JsonViewerProps = {
  value: unknown;
};

export function JsonViewer({ value }: JsonViewerProps) {
  return <pre className="json-viewer">{JSON.stringify(value, null, 2)}</pre>;
}
