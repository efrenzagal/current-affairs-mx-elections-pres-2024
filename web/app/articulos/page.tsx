"use client";

import { useEffect, useState } from "react";

import { SITE_NAME, SiteFooter, SiteHeader } from "../site-chrome";

type ArticleEntry = {
  slug: string;
  href: string;
  title: string;
  subtitle: string;
  author: string;
  published: string;
  summary: string;
  topics: string[];
};

function longDate(date: string) {
  return new Intl.DateTimeFormat("es-MX", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date(`${date}T12:00:00`));
}

export default function ArticlesPage() {
  const [articles, setArticles] = useState<ArticleEntry[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/data/articles.json")
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then((payload: ArticleEntry[]) => {
        if (!cancelled) setArticles(payload);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <main className="state-screen">
        <p className="eyebrow">{SITE_NAME}</p>
        <h1>No pudimos cargar los artículos.</h1>
        <p>Actualiza la página para intentarlo de nuevo.</p>
      </main>
    );
  }

  return (
    <main>
      <SiteHeader active="articulos" status="Artículos" />

      <section className="articles-hero">
        <div>
          <p className="eyebrow">Análisis</p>
          <h1>Artículos.</h1>
        </div>
        <p className="hero-copy">
          Piezas largas sobre política mexicana, escritas sobre el mismo almacén de datos que
          alimenta las visualizaciones. Cada gráfica es interactiva: puedes acercarte, filtrar y
          abrirla a pantalla completa.
        </p>
      </section>

      <section className="article-list">
        {articles === null ? (
          <p className="article-loading" aria-live="polite">
            Cargando artículos…
          </p>
        ) : articles.length === 0 ? (
          <p className="article-loading">Todavía no hay artículos publicados.</p>
        ) : (
          articles.map((article) => (
            <article key={article.slug} className="article-card">
              <div className="article-meta">
                <time dateTime={article.published}>{longDate(article.published)}</time>
                <span>{article.author}</span>
              </div>
              <div className="article-body">
                <h2>
                  <a href={article.href}>{article.title}</a>
                </h2>
                <p className="article-subtitle">{article.subtitle}</p>
                <p className="article-summary">{article.summary}</p>
                <div className="article-topics">
                  {article.topics.map((topic) => (
                    <span key={topic}>{topic}</span>
                  ))}
                </div>
                <a className="article-cta" href={article.href}>
                  Leer el artículo →
                </a>
              </div>
            </article>
          ))
        )}
      </section>

      <SiteFooter note="Artículos" />
    </main>
  );
}
