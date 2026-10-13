import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type AuthUser } from "./api";
import type { LiveFeedStatus, LiveQuote, MoexBond, MoexBondSearchResult, MoexRiskResult, Side } from "./types";

function money(value: string | number, currency = "RUB") {
  const amount = typeof value === "number" ? value : Number(value);
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

function signedMoney(value: string | number, currency = "RUB") {
  const amount = Number(value);
  const formatted = money(Math.abs(amount), currency);
  return amount > 0 ? `+${formatted}` : amount < 0 ? `−${formatted}` : formatted;
}

function compactMoney(value: string | number) {
  const amount = Number(value);
  const absolute = Math.abs(amount);
  const scaled = absolute >= 1_000_000
    ? `${(absolute / 1_000_000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} млн`
    : absolute >= 1_000
      ? `${(absolute / 1_000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} тыс.`
      : absolute.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
  return `${amount > 0 ? "+" : amount < 0 ? "−" : ""}${scaled}`;
}

function AuthScreen({ onAuthenticated }: { onAuthenticated: (user: AuthUser) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const user = mode === "login"
        ? await api.login(email, password)
        : await api.register(email, password);
      onAuthenticated(user);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось выполнить запрос.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-screen">
      <section className="auth-card">
        <a className="brand auth-brand" href="#top" aria-label="Риск-метрики">
          <span className="brand-mark"><BrandIcon /></span>
          <span className="brand-name">risk<span>metrics</span></span>
        </a>
        <div className="section-kicker">ЛИЧНЫЙ КАБИНЕТ</div>
        <h1>{mode === "login" ? "Вход в систему" : "Создание аккаунта"}</h1>
        <p className="auth-description">Войдите, чтобы собрать портфель облигаций и рассчитать его рыночный риск по актуальным котировкам.</p>
        <form onSubmit={submit} className="auth-form">
          <label>Email<input type="email" autoComplete="email" required maxLength={320} value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@example.com" /></label>
          <label>Пароль<input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={12} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Не менее 12 символов" /></label>
          {error && <div className="auth-error" role="alert">{error}</div>}
          <button className="primary-button auth-submit" type="submit" disabled={busy}>
            {busy ? <><span className="button-spinner" />Подождите…</> : mode === "login" ? "Войти" : "Зарегистрироваться"}
          </button>
        </form>
        <div className="auth-switch">
          {mode === "login" ? "Ещё нет аккаунта?" : "Уже есть аккаунт?"}
          <button onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(null); }}>
            {mode === "login" ? "Зарегистрироваться" : "Войти"}
          </button>
        </div>
        <p className="auth-footnote">Учебный MVP · котировки Finam Trade API, архивные сценарии MOEX</p>
      </section>
    </main>
  );
}

function App() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [serviceReady, setServiceReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [logoutError, setLogoutError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api.me()
      .then((currentUser) => active && setUser(currentUser))
      .catch(() => active && setUser(null))
      .finally(() => active && setAuthLoading(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!user) return;
    let active = true;
    setLoading(true);
    api.readiness()
      .then(() => active && setServiceReady(true))
      .catch(() => active && setServiceReady(false))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [user]);

  async function logout() {
    setLogoutError(null);
    try {
      await api.logout();
      setUser(null);
    } catch (cause) {
      setLogoutError(cause instanceof Error ? cause.message : "Не удалось завершить сеанс.");
    }
  }

  if (authLoading) {
    return <div className="loading-screen"><span className="spinner" />Проверяем сеанс…</div>;
  }
  if (!user) return <AuthScreen onAuthenticated={setUser} />;
  if (loading) {
    return <div className="loading-screen"><span className="spinner" />Подключаемся к рыночным данным…</div>;
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Риск-метрики, главная">
          <span className="brand-mark"><BrandIcon /></span>
          <span className="brand-name">risk<span>metrics</span></span>
        </a>
        <div className="topbar-right">
          <span className="environment-pill"><span className="dot" />Демонстрационный контур</span>
          <span className={`service-state ${serviceReady ? "is-ready" : "is-down"}`}>
            <span className="dot" />{serviceReady ? "Сервис доступен" : "Нет связи с API"}
          </span>
          <div className="account-control">
            <span className="account-email">{user.email}</span>
            <button className="logout-button" onClick={logout}>Выйти</button>
          </div>
        </div>
      </header>

      <main id="top" className="page-content">
        <section className="page-heading">
          <div>
            <div className="eyebrow"><span className="eyebrow-line" />АНАЛИТИКА РЫНКА</div>
            <h1>Риск облигационного портфеля</h1>
            <p className="page-subtitle">Котировки обновляются потоком Finam; VaR/ES используют архивные сценарии и кривую MOEX</p>
          </div>
        </section>
        {logoutError && <div className="error-banner" role="alert">{logoutError}</div>}
        <MoexRiskPanel />
        <footer className="page-footer"><span>RISK METRICS <b>·</b> MVP</span><span>Live-котировки — Finam Trade API · история и кривая — MOEX <i /> RUB</span></footer>
      </main>
    </div>
  );
}

function MoexRiskPanel() {
  const [bonds, setBonds] = useState<MoexBond[]>([]);
  const [liveQuotes, setLiveQuotes] = useState<LiveQuote[]>([]);
  const [liveStatus, setLiveStatus] = useState<LiveFeedStatus | null>(null);
  const [drafts, setDrafts] = useState<Array<{ secid: string; value: string; side: Side }>>([]);
  const [result, setResult] = useState<MoexRiskResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isinQuery, setIsinQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MoexBondSearchResult[]>([]);
  const [searchBusy, setSearchBusy] = useState(false);
  const [addingSecid, setAddingSecid] = useState<string | null>(null);
  const [searchMessage, setSearchMessage] = useState<string | null>(null);
  const lastCalculationKey = useRef("");
  const lastAutoCalculationAt = useRef(0);

  const now = Date.now();
  const quoteBySecid = new Map(liveQuotes.map((quote) => [quote.secid, quote]));
  const feedIsLive = Boolean(
    liveStatus?.connected && liveStatus.last_update &&
    now - new Date(liveStatus.last_update).getTime() >= -60_000 &&
    now - new Date(liveStatus.last_update).getTime() <= 300_000
  );

  useEffect(() => {
    let active = true;
    api.moexInstruments()
      .then(({ items }) => {
        if (!active) return;
        setBonds(items);
        if (items.length) setDrafts([{ secid: items[0].secid, value: "1000000.00", side: "long" }]);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "Не удалось загрузить выпуски MOEX.");
      })
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const [status, quoteResponse] = await Promise.all([api.liveStatus(), api.liveQuotes()]);
        if (active) {
          setLiveStatus(status);
          setLiveQuotes(quoteResponse.items);
        }
      } catch {
        if (active) setLiveStatus((current) => current ? { ...current, connected: false } : current);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  const used = new Set(drafts.map((trade) => trade.secid));
  function update(index: number, changes: Partial<(typeof drafts)[number]>) {
    setDrafts((current) => current.map((trade, item) => item === index ? { ...trade, ...changes } : trade));
    setResult(null);
  }

  function quoteSignature() {
    return drafts.map((trade) => `${trade.secid}:${quoteBySecid.get(trade.secid)?.quote_at ?? "no-live"}`).join("|");
  }
  function add() {
    const next = bonds.find((bond) => !used.has(bond.secid));
    if (next) setDrafts((current) => [...current, { secid: next.secid, value: "1000000.00", side: "long" }]);
    setResult(null);
  }
  async function searchByIsin(event: FormEvent) {
    event.preventDefault();
    setSearchMessage(null);
    setSearchResults([]);
    const normalized = isinQuery.trim().toUpperCase();
    if (!/^[A-Z]{2}[A-Z0-9]{10}$/.test(normalized)) {
      setSearchMessage("Введите ISIN из 12 символов.");
      return;
    }
    setSearchBusy(true);
    try {
      const { items } = await api.searchMoexBonds(normalized);
      setSearchResults(items);
      if (!items.length) setSearchMessage("В MOEX ISS облигация с таким ISIN не найдена.");
    } catch (cause) {
      setSearchMessage(cause instanceof Error ? cause.message : "Не удалось выполнить поиск в MOEX ISS.");
    } finally {
      setSearchBusy(false);
    }
  }
  async function addSearchedBond(candidate: MoexBondSearchResult) {
    if (drafts.length >= 20 || used.has(candidate.secid)) return;
    setAddingSecid(candidate.secid);
    setSearchMessage(null);
    try {
      await api.addMoexBond(candidate.isin, candidate.secid);
      const { items } = await api.moexInstruments();
      setBonds(items);
      setDrafts((current) => current.some((trade) => trade.secid === candidate.secid)
        ? current
        : [...current, { secid: candidate.secid, value: "1000000.00", side: "long" }]);
      setSearchResults((current) => current.map((item) => item.secid === candidate.secid
        ? { ...item, already_added: true }
        : item));
      setSearchMessage(`${candidate.short_name} добавлена в портфель.`);
      setResult(null);
    } catch (cause) {
      setSearchMessage(cause instanceof Error ? cause.message : "Не удалось добавить облигацию из MOEX ISS.");
    } finally {
      setAddingSecid(null);
    }
  }
  async function calculate() {
    setError(null);
    setBusy(true);
    lastCalculationKey.current = quoteSignature();
    try {
      setResult(await api.calculateMoexRisk(drafts.map((trade) => ({
        secid: trade.secid,
        position_market_value: trade.value,
        side: trade.side,
      }))));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось выполнить расчёт MOEX.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!result || busy || loading || !drafts.length) return;
    const key = quoteSignature();
    if (!key || key === lastCalculationKey.current || (key.includes("no-live") && !result.live_prices_used)) return;
    if (Date.now() - lastAutoCalculationAt.current < 8000) return;
    lastCalculationKey.current = key;
    lastAutoCalculationAt.current = Date.now();
    setBusy(true);
    api.calculateMoexRisk(drafts.map((trade) => ({
      secid: trade.secid,
      position_market_value: trade.value,
      side: trade.side,
    })))
      .then(setResult)
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Не удалось обновить риск по live-котировке."))
      .finally(() => setBusy(false));
  }, [liveQuotes, result, busy, loading, drafts]);

  return (
    <section className="moex-risk-section">
      <div className="moex-risk-title">
        <div>
          <div className="section-kicker">LIVE-КОТИРОВКИ FINAM · РИСКОВАЯ ИСТОРИЯ MOEX</div>
          <h2>Портфель и метрики риска</h2>
          <p>Цена берётся из последней доступной котировки Finam или MOEX; кривая и сценарии VaR/ES — из истории MOEX. Сторону и размер позиции задаёте вы.</p>
        </div>
        <span className={`feed-pill ${feedIsLive ? "is-live" : "is-off"}`}><i />{feedIsLive ? "Finam live" : liveQuotes.length ? "Последние доступные цены" : liveStatus?.enabled ? "Нет котировки" : "Live не настроен"}</span>
      </div>
      {liveStatus && !liveStatus.enabled && <div className="live-help">Чтобы включить поток, задайте FINAM_SECRET в локальном файле .env и перезапустите Docker.</div>}
      {liveStatus?.enabled && !feedIsLive && <div className="live-help">Свежего тика Finam сейчас нет. Расчёт использует последнюю сохранённую котировку Finam или MOEX; источник и время показаны у каждого выпуска. Если котировки ещё нет, берётся архивное закрытие MOEX.</div>}
      <div className="panel moex-editor">
        {loading && <div className="moex-empty"><span className="spinner" />Загружаем выпуски MOEX…</div>}
        {!loading && !error && bonds.length === 0 && <div className="moex-empty">В базе пока нет выпусков. Импортируйте сохранённый набор данных MOEX.</div>}
        <form className="moex-search" onSubmit={searchByIsin}>
          <label htmlFor="bond-isin-search">ПОИСК ОБЛИГАЦИИ ПО ISIN</label>
          <div className="moex-search-field">
            <input
              id="bond-isin-search"
              value={isinQuery}
              onChange={(event) => setIsinQuery(event.target.value.toUpperCase())}
              placeholder="Например, RU000A0JS3W6"
              maxLength={12}
              aria-label="ISIN облигации для поиска"
            />
            <button className="add-trade" type="submit" disabled={searchBusy || isinQuery.trim().length !== 12}>
              {searchBusy ? "Ищем…" : "Найти в MOEX"}
            </button>
          </div>
          {searchResults.map((candidate) => {
            const inPortfolio = used.has(candidate.secid);
            const alreadyLoaded = candidate.already_added || bonds.some((bond) => bond.secid === candidate.secid);
            return <div className="moex-search-result" key={candidate.secid}>
              <span><strong>{candidate.short_name}</strong><small>{candidate.name} · {candidate.isin} · {candidate.secid}{candidate.primary_boardid ? ` · ${candidate.primary_boardid}` : ""}</small></span>
              <button className="add-trade" type="button" disabled={!candidate.is_traded || inPortfolio || drafts.length >= 20 || addingSecid === candidate.secid} onClick={() => void addSearchedBond(candidate)}>
                {addingSecid === candidate.secid ? "Загружаем историю…" : inPortfolio ? "Уже в портфеле" : alreadyLoaded ? "＋ Добавить" : "＋ Загрузить и добавить"}
              </button>
            </div>;
          })}
          {searchMessage && <small className="moex-search-message" role="status">{searchMessage}</small>}
        </form>
        {drafts.map((trade, index) => {
          const bond = bonds.find((item) => item.secid === trade.secid);
          const quote = quoteBySecid.get(trade.secid);
          return <div className="moex-trade" key={`${trade.secid}-${index}`}>
            <label>
              <span>ВЫПУСК</span>
              <select value={trade.secid} onChange={(event) => update(index, { secid: event.target.value })}>
                {bonds.filter((item) => item.secid === trade.secid || !used.has(item.secid)).map((item) => (
                  <option key={item.secid} value={item.secid}>{item.short_name} · {item.secid}</option>
                ))}
              </select>
              <small>{bond?.name} · ISIN {bond?.isin} · {quote ? `${quote.source} ${quote.last_price_pct}% · ${new Date(quote.quote_at).toLocaleString("ru-RU")}` : `архив MOEX ${bond?.latest_price_pct ?? "—"}% · ${bond?.latest_trade_date ?? "дата неизвестна"}`}</small>
            </label>
            <label><span>СТОРОНА</span><select value={trade.side} onChange={(event) => update(index, { side: event.target.value as Side })}><option value="long">Long · длинная</option><option value="short">Short · короткая</option></select></label>
            <label><span>РЫНОЧНАЯ СТОИМОСТЬ ПОЗИЦИИ</span><div className="amount-field"><input inputMode="decimal" value={trade.value} onChange={(event) => update(index, { value: event.target.value.replace(/,/g, ".") })} aria-label="Рыночная стоимость позиции в рублях" /><span>₽</span></div></label>
            <button className="icon-button delete-button" disabled={drafts.length === 1} onClick={() => { setDrafts((current) => current.filter((_, item) => item !== index)); setResult(null); }} aria-label="Удалить позицию">×</button>
          </div>;
        })}
        <div className="moex-actions">
          <button className="add-trade" onClick={add} disabled={drafts.length >= bonds.length || !bonds.length}>＋ Добавить выпуск</button>
          <span>{busy ? "Обновляем риск по котировкам…" : "Автообновление риска при новых котировках · до 500 сценариев"}</span>
          <button className="primary-button" onClick={calculate} disabled={busy || loading || !drafts.length || !bonds.length}>
            {busy ? "Считаем…" : <>Рассчитать VaR, ES, KRD и CS01 <ArrowIcon /></>}
          </button>
        </div>
        {error && <div className="error-banner" role="alert">{error}</div>}
      </div>
      {result && <div className="moex-output">
        <div className="metric-cards moex-metrics">
          <article className="metric-card"><span className="metric-name">1-ДНЕВНЫЙ VaR 99%</span><strong>{money(result.var_1d)}</strong><small>Историческое моделирование · {result.lookback_observations} сценариев</small></article>
          <article className="metric-card"><span className="metric-name">EXPECTED SHORTFALL 99%</span><strong>{money(result.expected_shortfall_1d)}</strong><small>Средний убыток за порогом VaR</small></article>
          <article className="metric-card"><span className="metric-name">DV01 ПОРТФЕЛЯ</span><strong>{signedMoney(result.dv01_value_change_per_bp)}</strong><small>Изменение стоимости при +1 б.п.</small></article>
          <article className="metric-card"><span className="metric-name">CS01 ПОРТФЕЛЯ</span><strong>{signedMoney(result.cs01_value_change_per_bp)}</strong><small>Кредитный спред +1 б.п. · ОФЗ исключены</small></article>
        </div>
        <div className="moex-charts">
          <LossDistributionChart result={result} />
          <KeyRateChart items={result.key_rate_dv01} />
        </div>
        <div className="panel moex-detail">
          <div className="panel-heading compact-heading"><div><div className="section-kicker">КРИВАЯ СТАВОК</div><h3>Key Rate DV01</h3></div><span className="scenario-tag">Переоценка при +1 б.п.</span></div>
          <div className="moex-krd-grid">{result.key_rate_dv01.map((item) => <div key={item.tenor}><span>{item.tenor}</span><strong>{compactMoney(item.value_change_per_bp)}</strong></div>)}</div>
          <div className="detail-table-wrap"><table className="detail-table"><thead><tr><th>ВЫПУСК</th><th>СТОРОНА</th><th>РЫНОЧНАЯ СТОИМОСТЬ</th><th>Z-SPREAD</th><th>CS01</th></tr></thead><tbody>{result.trades.map((trade) => <tr key={trade.secid}><td><strong>{trade.short_name}</strong><small>{trade.secid} · {trade.z_spread_source}</small></td><td><span className={`direction-chip ${trade.side}`}>{trade.side === "long" ? "Long" : "Short"}</span></td><td>{money(trade.market_value)}</td><td>{trade.z_spread_pct === null ? "—" : `${trade.z_spread_pct.toFixed(3)}%`}</td><td>{trade.cs01_value_change_per_bp === null ? "—" : signedMoney(trade.cs01_value_change_per_bp)}</td></tr>)}</tbody></table></div>
          <div className="detail-footer"><span>Цена: последняя доступная котировка Finam/MOEX · сценарии, кривая и денежные потоки: архив MOEX</span><span>Цена {result.live_prices_used ? new Date(result.as_of).toLocaleString("ru-RU") : result.as_of} · кривая {result.curve_as_of}</span></div>
        </div>
      </div>}
    </section>
  );
}

function LossDistributionChart({ result }: { result: MoexRiskResult }) {
  const width = 720, height = 250;
  const margin = { top: 26, right: 18, bottom: 42, left: 42 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const losses = result.pnl_scenarios.map((scenario) => scenario.loss).filter(Number.isFinite);
  if (!losses.length) return null;
  const minimum = Math.min(0, ...losses);
  const maximum = Math.max(result.expected_shortfall_1d, result.var_1d, ...losses);
  const span = Math.max(maximum - minimum, 1);
  const binCount = Math.min(24, Math.max(10, Math.ceil(Math.sqrt(losses.length))));
  const bins = Array.from({ length: binCount }, () => 0);
  for (const loss of losses) {
    const index = Math.min(binCount - 1, Math.max(0, Math.floor(((loss - minimum) / span) * binCount)));
    bins[index] += 1;
  }
  const peak = Math.max(...bins, 1);
  const x = (value: number) => margin.left + ((value - minimum) / span) * plotWidth;
  const baseline = margin.top + plotHeight;
  const varX = x(result.var_1d), esX = x(result.expected_shortfall_1d);
  const tickValues = [0, Math.ceil(peak / 2), peak].filter((value, index, items) => items.indexOf(value) === index);
  return <article className="panel risk-chart-panel">
    <div className="panel-heading compact-heading"><div><div className="section-kicker">ИСТОРИЧЕСКОЕ МОДЕЛИРОВАНИЕ · {result.confidence * 100}%</div><h3>Распределение дневных потерь</h3></div><span className="scenario-tag">{losses.length} сценариев</span></div>
    <p className="chart-description">Распределение дневного P&L в виде убытка: отрицательные значения соответствуют прибыли. Линия ES показывает средний убыток в хвосте за порогом VaR.</p>
    <div className="risk-chart-scroll"><svg className="risk-chart-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Распределение исторических потерь по ${losses.length} сценариям. VaR ${money(result.var_1d)}, Expected Shortfall ${money(result.expected_shortfall_1d)}.`}>
      {tickValues.map((tick) => { const y = baseline - (tick / peak) * plotHeight; return <g key={tick}><line x1={margin.left} x2={width - margin.right} y1={y} y2={y} className="chart-gridline"/><text x={margin.left - 8} y={y + 3} textAnchor="end" className="chart-axis-label">{tick}</text></g>; })}
      {bins.map((count, index) => { const barWidth = plotWidth / binCount; const barHeight = (count / peak) * plotHeight; return <rect key={index} x={margin.left + index * barWidth + 1} y={baseline - barHeight} width={Math.max(1, barWidth - 2)} height={barHeight} rx="2" className="loss-bin"><title>{count} сценариев</title></rect>; })}
      <line x1={varX} x2={varX} y1={margin.top} y2={baseline} className="var-marker"/><line x1={esX} x2={esX} y1={margin.top} y2={baseline} className="es-marker"/>
      <text x={Math.min(varX + 4, width - 92)} y={margin.top - 8} className="chart-var-label">VaR {compactMoney(result.var_1d)}</text><text x={Math.min(esX + 4, width - 92)} y={margin.top + 8} className="chart-es-label">ES {compactMoney(result.expected_shortfall_1d)}</text>
      <line x1={margin.left} x2={width - margin.right} y1={baseline} y2={baseline} className="chart-axis"/>
      <text x={margin.left} y={height - 12} textAnchor="start" className="chart-axis-label">{compactMoney(minimum)} ₽</text><text x={width - margin.right} y={height - 12} textAnchor="end" className="chart-axis-label">{compactMoney(maximum)} ₽</text>
    </svg></div>
    <div className="risk-chart-legend"><span><i className="legend-histogram"/>Исторические сценарии</span><span><i className="legend-var"/>VaR</span><span><i className="legend-es"/>Expected Shortfall</span></div>
  </article>;
}

function KeyRateChart({ items }: { items: MoexRiskResult["key_rate_dv01"] }) {
  const width = 720, height = 250;
  const margin = { top: 24, right: 16, bottom: 44, left: 16 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const maxAbs = Math.max(1, ...items.map((item) => Math.abs(item.value_change_per_bp)));
  const baseline = margin.top + plotHeight / 2;
  const halfHeight = plotHeight / 2 - 4;
  const slot = plotWidth / Math.max(items.length, 1);
  return <article className="panel risk-chart-panel">
    <div className="panel-heading compact-heading"><div><div className="section-kicker">ЧУВСТВИТЕЛЬНОСТЬ К КРИВОЙ MOEX</div><h3>Key Rate DV01 по срокам</h3></div><span className="scenario-tag">+1 б.п. в узле кривой</span></div>
    <p className="chart-description">Вклад каждого срока в изменение стоимости портфеля при сдвиге соответствующего узла доходности.</p>
    <div className="risk-chart-scroll"><svg className="risk-chart-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Столбчатая диаграмма Key Rate DV01 по узлам кривой ставок MOEX.">
      <line x1={margin.left} x2={width - margin.right} y1={baseline} y2={baseline} className="chart-zero"/>
      {items.map((item, index) => {
        const center = margin.left + slot * (index + 0.5), value = item.value_change_per_bp;
        const barHeight = Math.abs(value) / maxAbs * halfHeight, barWidth = Math.min(30, slot * 0.58);
        const y = value >= 0 ? baseline - barHeight : baseline;
        return <g key={item.tenor}><rect x={center - barWidth / 2} y={y} width={barWidth} height={Math.max(barHeight, 1)} rx="3" className={value >= 0 ? "krd-positive" : "krd-negative"}><title>{item.tenor}: {money(value)} на 1 б.п.</title></rect><text x={center} y={height - 20} textAnchor="middle" className="chart-tenor-label">{item.tenor}</text></g>;
      })}
    </svg></div>
    <div className="risk-chart-legend"><span><i className="krd-legend-positive"/>Положительный вклад</span><span><i className="krd-legend-negative"/>Отрицательный вклад</span><span className="chart-note">Знак показывает направление изменения стоимости</span></div>
  </article>;
}

function BrandIcon() { return <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 22.5 12.2 15l5 4.4L27 8.5" /><path d="M19.5 8.5H27v7.3" /><circle cx="5" cy="22.5" r="2" /><circle cx="12.2" cy="15" r="2" /><circle cx="17.2" cy="19.4" r="2" /></svg>; }
function ArrowIcon() { return <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10h11M10 5l5 5-5 5" /></svg>; }

export default App;
