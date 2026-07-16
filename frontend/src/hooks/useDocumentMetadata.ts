import { useEffect } from 'react';

/**
 * A custom hook to dynamically update the document title, meta description, and keywords
 * for SEO purposes inside the React SPA.
 * 
 * @param title The page title to set.
 * @param description The meta description content to set.
 * @param keywords The comma-separated meta keywords content to set.
 */
export function useDocumentMetadata(
  title: string,
  description: string,
  keywords: string
) {
  useEffect(() => {
    // 1. Update document title
    const prevTitle = document.title;
    document.title = title;

    // Helper to find or create a meta tag
    const updateMetaTag = (name: string, content: string) => {
      let element = document.querySelector(`meta[name="${name}"]`);
      if (!element) {
        element = document.createElement('meta');
        element.setAttribute('name', name);
        document.head.appendChild(element);
      }
      const prevContent = element.getAttribute('content') || '';
      element.setAttribute('content', content);
      return prevContent;
    };

    // 2. Update meta description
    const prevDescription = updateMetaTag('description', description);

    // 3. Update meta keywords
    const prevKeywords = updateMetaTag('keywords', keywords);

    // Clean up to restore original tags on unmount (standard practice for React routers)
    return () => {
      document.title = prevTitle;
      updateMetaTag('description', prevDescription);
      updateMetaTag('keywords', prevKeywords);
    };
  }, [title, description, keywords]);
}
