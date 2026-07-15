import SuggestionForm from "@/components/copy-trade/SuggestionForm";

export const dynamic = "force-dynamic";

// Tela "Sugestões" (Copy Trade): o operador cola endereços de wallet e roda o
// pipeline de discovery para cada um (deep dive → simulação → filtros → score →
// coorte). Passo 1 (Analisar) não grava nada; passo 2 (Salvar) grava as
// selecionadas como SUGERIDO com origin="usuário". Curadoria manual: pode
// salvar mesmo wallets que reprovam filtros (força-salvar).
export default function SuggestionsPage() {
  return (
    <section>
      <div className="pagehead">
        <div>
          <div className="eyebrow">Estratégias · copy trade</div>
          <h1>Sugestões</h1>
        </div>
      </div>
      <SuggestionForm />
    </section>
  );
}
