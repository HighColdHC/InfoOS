import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function ReaderMarkdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ node: _node, href, children, ...props }) => <a {...props} href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
      }}
    >{children}</ReactMarkdown>
  );
}
