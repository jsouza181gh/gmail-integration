import { useQuery } from '@tanstack/react-query'
import { categories } from '../../api/endpoints'
import { Empty } from '../../ui/Empty'

export function Categorias() {
  const taxonomy = useQuery({ queryKey: ['categories'], queryFn: categories })
  return (
    <section className="ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Categorias">
      <h2 className="h-heading">{taxonomy.data ? `Categorias, taxonomia v${taxonomy.data.version}` : 'Categorias'}</h2>
      {taxonomy.isError ? <Empty text="Não foi possível carregar as categorias." /> : null}
      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))' }}>
        {taxonomy.data?.categories.map((c) => (
          <div key={c.name} className="ia-row" style={{ gridTemplateColumns: '1fr', cursor: 'default' }}>
            <span className="h-heading" style={{ fontSize: 14 }}>{c.name}</span>
            <span className="t-small ia-muted">{c.definition}</span>
          </div>
        ))}
      </div>
      {taxonomy.data && taxonomy.data.flags.length > 0 ? (
        <>
          <h3 className="h-heading">Marcações</h3>
          <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))' }}>
            {taxonomy.data.flags.map((f) => (
              <div key={f.name} className="ia-row" style={{ gridTemplateColumns: '1fr', cursor: 'default' }}>
                <span className="h-heading" style={{ fontSize: 14 }}>{f.name}</span>
                <span className="t-small ia-muted">{f.definition}</span>
              </div>
            ))}
          </div>
        </>
      ) : null}
      <p className="t-small ia-subtle" style={{ margin: 0 }}>
        Somente leitura por enquanto.
      </p>
    </section>
  )
}
