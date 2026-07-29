// =============================================================================
// Pattern Search Engine (PSE) - 归一化研盘工作台 UI 主程序 (App.tsx)
// 职责：实现极速扫描大PK、起始点归零百分比重合 Kline 绘制、高斯事件悬浮气泡标注、滚动无偏回测图表、自适应反馈闭环
// =============================================================================

import { useState, useEffect, useRef, type CSSProperties } from 'react';
import ReactECharts from 'echarts-for-react';
import {
  TrendingUp,
  BarChart2,
  Calendar,
  Settings,
  Play,
  RotateCcw,
  Star,
  Trash2,
  AlertTriangle,
  Award,
  Sliders,
  Sparkles,
  Server,
  Database,
  Info,
  Search,
  ChevronDown,
  ChevronUp
} from 'lucide-react';
import './App.css';

// -----------------------------------------------------------------------------
// 1. 类型定义 (TypeScript Interface)
// -----------------------------------------------------------------------------
interface Template {
  id: number;
  name: string;
  type: string;
  created_at: string;
  config?: any;
  weights?: Record<string, number>;
}

interface ScanResult {
  id: number;
  date: string;
  code: string;
  name: string;
  similarity_score: number;
  sub_scores: {
    trend_score: number;
    boll_score: number;
    volume_score: number;
    candle_score: number;
    volatility_score: number;
    event_score?: number;
  };
  explanation: string;
  risk_tips: string;
}

interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  boll_mid?: number;
  boll_upper?: number;
  boll_lower?: number;
  pct_b?: number | null;
}

interface StockSearchItem {
  code: string;
  name: string;
  board?: string;
  industry?: string;
}

interface BollPatternMatch {
  id: number;
  code: string;
  name?: string;
  pattern_id: string;
  pattern_name: string;
  start_date: string;
  end_date: string;
  matched_states: string;
  score: number | null;
  scan_date: string | null;
  window_days: number;
}

interface BollPatternMeta {
  id: string;
  name: string;
  enabled: boolean;
  regex?: string;
  min_total_days?: number;
  zone_thresholds?: Record<string, [string | number, string | number]> | null;
  denoise_min_len?: number | null;
  effective?: {
    zone_thresholds: Record<string, [string | number, string | number]>;
    denoise_min_len: number;
  };
}

interface BollScanProgress {
  running: boolean;
  phase: 'idle' | 'preparing' | 'scanning' | 'done' | 'failed' | string;
  current: number;
  total: number;
  scanned: number;
  skipped: number;
  errors: number;
  matches: number;
  percent: number;
  window_days: number | null;
  scan_date: string | null;
  current_code: string | null;
  message: string;
  started_at: string | null;
  finished_at: string | null;
  summary: Record<string, unknown> | null;
  error: string | null;
}

interface MatchedEvent {
  event_type: string;
  date: string;
  confidence: number;
  evidence: string;
}

interface ComparePayload {
  template_symbol: string;
  candidate_symbol: string;
  window_size: number;
  temp_bars: Bar[];
  cand_bars: Bar[];
  similarity_scores: {
    total_score: number;
    breakdown: Record<string, number>;
  };
  alignment_path: [number, number][];
  matched_events: MatchedEvent[];
  explanation_facts: {
    positive_facts: { field: string; text: string; confidence: number }[];
    negative_facts: { field: string; text: string; confidence: number }[];
  };
}

interface BacktestResult {
  backtest_id: string;
  template_id: number;
  summary: {
    total_signals: number;
    winning_rate_5d: number;
    winning_rate_10d: number;
    winning_rate_20d: number;
    avg_return_5d: number;
    avg_return_10d: number;
    avg_return_20d: number;
    avg_alpha_5d: number;
    avg_alpha_10d: number;
    avg_alpha_20d: number;
    profit_loss_ratio: number;
    max_drawdown: number;
  };
  equity_curve: {
    trade_date: string;
    portfolio_value: number;
    benchmark_value: number;
  }[];
  trade_details: any[];
}

type ZoneBounds = { m: number; h: number; u: number };

function isZoneBoundsOrdered(b: ZoneBounds): boolean {
  return (
    Number.isFinite(b.m) &&
    Number.isFinite(b.h) &&
    Number.isFinite(b.u) &&
    b.m < b.h &&
    b.h < b.u
  );
}

function formatZoneBound(n: number): string {
  if (!Number.isFinite(n)) return '—';
  // 去掉多余尾零，便于阅读
  return String(Number(n.toFixed(4)));
}

const BOLL_ZONE_LABEL: Record<string, string> = {
  L: '中轨下方',
  M: '贴中轨',
  H: '中上轨之间',
  U: '触及/突破上轨',
};

type BollRegexDescribe = {
  ok: boolean;
  text: string;
  kind: 'ok' | 'empty' | 'unsupported' | 'invalid';
};

function describeBollQuantifier(q: string): string {
  if (!q) return '1日';
  if (q === '+') return '持续1日以上';
  if (q === '*') return '可有可无';
  if (q === '?') return '可选1日';
  let m = q.match(/^\{(\d+)\}$/);
  if (m) return `${m[1]}日`;
  m = q.match(/^\{(\d+),\}$/);
  if (m) return `至少${m[1]}日`;
  m = q.match(/^\{(\d+),(\d+)\}$/);
  if (m) return `${m[1]}～${m[2]}日`;
  return '未知量词';
}

/** 将简单 L/M/H/U 编排正则译为「→」阶段链；复杂写法不硬译。 */
function describeBollRegex(regex: string): BollRegexDescribe {
  const raw = (regex || '').trim();
  if (!raw) return { ok: false, text: '请填写 regex', kind: 'empty' };

  try {
    // 与后端 re.compile 一致：先确认语法可编译
    // eslint-disable-next-line no-new
    new RegExp(raw);
  } catch {
    return { ok: false, text: '正则语法无效', kind: 'invalid' };
  }

  const compact = raw.replace(/\s+/g, '');
  const simpleFull = /^([LMHU](\+|\*|\?|\{\d+,\d+\}|\{\d+,\}|\{\d+\})?)+$/;
  if (!simpleFull.test(compact)) {
    return {
      ok: false,
      text: '暂不支持该写法（仅支持 L/M/H/U + 简单量词连续串）',
      kind: 'unsupported',
    };
  }

  const stages: string[] = [];
  const tokenRe = /([LMHU])(\+|\*|\?|\{\d+,\d+\}|\{\d+,\}|\{\d+\})?/g;
  let m: RegExpExecArray | null;
  while ((m = tokenRe.exec(compact)) !== null) {
    const label = BOLL_ZONE_LABEL[m[1]] || m[1];
    stages.push(`${label}（${describeBollQuantifier(m[2] || '')}）`);
  }
  if (stages.length === 0) {
    return { ok: false, text: '暂不支持该写法（仅支持 L/M/H/U + 简单量词连续串）', kind: 'unsupported' };
  }
  return { ok: true, text: stages.join(' → '), kind: 'ok' };
}

function bollRegexDescribeColor(kind: BollRegexDescribe['kind']): string {
  if (kind === 'ok') return 'var(--color-text-muted)';
  if (kind === 'empty') return 'var(--color-text-muted)';
  return '#fbbf24';
}

function ZoneBoundStepper({
  value,
  onChange,
  invalid = false,
  title,
}: {
  value: number;
  onChange: (n: number) => void;
  invalid?: boolean;
  title?: string;
}) {
  const step = 0.01;
  const bump = (dir: 1 | -1) => {
    const next = Math.round((value + dir * step) * 10000) / 10000;
    onChange(next);
  };
  const border = invalid ? '#ef4444' : 'rgba(96, 165, 250, 0.55)';
  const btnStyle: CSSProperties = {
    width: '1.7rem',
    height: '1.7rem',
    padding: 0,
    border: `1px solid ${border}`,
    borderRadius: '4px',
    background: 'rgba(59, 130, 246, 0.28)',
    color: '#e2e8f0',
    fontSize: '1rem',
    fontWeight: 700,
    lineHeight: 1,
    cursor: 'pointer',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  };

  return (
    <div className="zone-bound-stepper" style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }} title={title}>
      <button type="button" aria-label="减小" className="zone-bound-stepper__btn" style={btnStyle} onClick={() => bump(-1)}>−</button>
      <input
        type="number"
        step={step}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="zone-bound-stepper__input"
        style={{
          width: '4.2rem',
          height: '1.7rem',
          border: `1px solid ${border}`,
          borderRadius: '4px',
          padding: '0 0.3rem',
          textAlign: 'center',
          background: 'rgba(15, 23, 42, 0.85)',
          color: '#f8fafc',
          fontFamily: 'monospace',
          fontSize: '0.82rem',
        }}
      />
      <button type="button" aria-label="增大" className="zone-bound-stepper__btn" style={btnStyle} onClick={() => bump(1)}>+</button>
    </div>
  );
}

/** 四行分区编辑器：行上展示含义/区间/贴轨锚点；L/M/H 行编辑交界分界点 */
function ZoneBoundsEditor({
  bounds,
  onChange,
  showInvalid = false,
}: {
  bounds: ZoneBounds;
  onChange: (next: ZoneBounds) => void;
  showInvalid?: boolean;
}) {
  const invalid = showInvalid && !isZoneBoundsOrdered(bounds);

  const rows: Array<{
    key: 'L' | 'M' | 'H' | 'U';
    title: string;
    rangeText: string;
    anchor: string;
    editKey?: keyof ZoneBounds;
    editHint?: string;
  }> = [
    {
      key: 'L',
      title: 'L · 中轨下方',
      rangeText: `−∞ ≤ %B < ${formatZoneBound(bounds.m)}`,
      anchor: '贴下轨 = 0',
      editKey: 'm',
      editHint: 'L↔M 分界',
    },
    {
      key: 'M',
      title: 'M · 贴中轨',
      rangeText: `${formatZoneBound(bounds.m)} ≤ %B < ${formatZoneBound(bounds.h)}`,
      anchor: '贴中轨 = 0.5',
      editKey: 'h',
      editHint: 'M↔H 分界',
    },
    {
      key: 'H',
      title: 'H · 中轨与上轨之间',
      rangeText: `${formatZoneBound(bounds.h)} ≤ %B < ${formatZoneBound(bounds.u)}`,
      anchor: '过渡区',
      editKey: 'u',
      editHint: 'H↔U 分界',
    },
    {
      key: 'U',
      title: 'U · 触及/突破上轨',
      rangeText: `${formatZoneBound(bounds.u)} ≤ %B < +∞`,
      anchor: '贴上轨 = 1',
    },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', maxWidth: '420px', width: '100%' }}>
      <p style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', margin: 0, lineHeight: 1.45 }}>
        %B = (close − lower) / (upper − lower)；左闭右开。跌破下轨 &lt;0，突破上轨 &gt;1。
      </p>
      {rows.map(row => (
        <div
          key={row.key}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '0.75rem',
            padding: '0.5rem 0.65rem',
            border: `1px solid ${invalid && row.editKey ? '#ef4444' : 'var(--border-color)'}`,
            borderRadius: '6px',
            background: 'rgba(148, 163, 184, 0.06)',
          }}
        >
          <div style={{ minWidth: 0, flex: '1 1 auto' }}>
            <div style={{ fontSize: '0.78rem', color: 'var(--color-text-main)', fontWeight: 600 }}>
              {row.title}
            </div>
            <div style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', fontFamily: 'monospace', marginTop: '0.12rem' }}>
              {row.rangeText}
            </div>
            <div style={{ fontSize: '0.68rem', color: 'var(--color-text-muted)', marginTop: '0.1rem' }}>
              {row.anchor}
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '0.2rem', flexShrink: 0 }}>
            {row.editKey ? (
              <>
                <span style={{ fontSize: '0.65rem', color: 'var(--color-text-muted)' }}>{row.editHint}</span>
                <ZoneBoundStepper
                  value={bounds[row.editKey]}
                  invalid={invalid}
                  title={row.editHint}
                  onChange={n => onChange({ ...bounds, [row.editKey!]: n })}
                />
              </>
            ) : (
              <span style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', fontFamily: 'monospace' }}>+∞</span>
            )}
          </div>
        </div>
      ))}
      {invalid && (
        <p style={{ fontSize: '0.7rem', color: '#f87171', margin: 0 }}>
          分界须满足 L↔M &lt; M↔H &lt; H↔U（即 m &lt; h &lt; u）
        </p>
      )}
    </div>
  );
}

export default function App() {
  // -----------------------------------------------------------------------------
  // 2. 状态管理 (State Management)
  // -----------------------------------------------------------------------------
  const [apiBase, setApiBase] = useState('http://localhost:8000');
  const [activeTab, setActiveTab] = useState<'scan' | 'backtest' | 'templates' | 'settings' | 'boll_pattern' | 'stock_view'>('scan');
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncingToday, setSyncingToday] = useState(false);

  // 扩展可配置参数（持久化存储到本地 localStorage，重启后自动无缝读取）
  const [windowSize, setWindowSize] = useState(() => Number(localStorage.getItem('window_size') || '60'));
  const [maxWorkers, setMaxWorkers] = useState(() => Number(localStorage.getItem('max_workers') || '8'));
  const [delayMin, setDelayMin] = useState(() => Number(localStorage.getItem('delay_min') || '100'));
  const [delayMax, setDelayMax] = useState(() => Number(localStorage.getItem('delay_max') || '300'));
  const [retryLimit, setRetryLimit] = useState(() => Number(localStorage.getItem('retry_limit') || '3'));
  const [learningRate, setLearningRate] = useState(() => Number(localStorage.getItem('learning_rate') || '0.05'));

  // 数据库及同步日志全局状态
  const [dbStats, setDbStats] = useState({ total_stocks: 0, total_bars: 0, latest_bar_date: 'N/A' });
  const [liveLogs, setLiveLogs] = useState<string[]>(['⚙️ 等待一键同步命令激活...']);

  // 形态模板前台注册管理表单状态
  const [newTplName, setNewTplName] = useState('');
  const [newTplSymbol, setNewTplSymbol] = useState('');
  const [newTplEndDate, setNewTplEndDate] = useState('2026-07-19');
  const [newTplWindowSize, setNewTplWindowSize] = useState(60);
  const [resolvedStockName, setResolvedStockName] = useState(''); // 实时代码转中文名
  const [weightsMap, setWeightsMap] = useState<Record<string, number>>({
    close_norm: 0.25,
    boll_mid_dist: 0.20,
    volume_ratio_20: 0.15,
    close_position: 0.15,
    return_5d: 0.10,
    range_ratio: 0.10,
    atr_ratio: 0.05
  });

  // 侧滑抽屉与局部比对加载控制状态
  const [compareLoading, setCompareLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [chartView, setChartView] = useState<'compare' | 'boll_kline'>('compare');

  // 模板数据
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | null>(null);
  const [selectedTemplateDetail, setSelectedTemplateDetail] = useState<Template | null>(null);

  // 模板 Schema（后端元数据）
  const [templateSchema, setTemplateSchema] = useState<any>(null);

  // 内置事件类型兜底（schema 未加载时也能显示选项）
  const defaultEventTypes = [
    { key: "TREND_UP", name: "上升趋势" },
    { key: "TOUCH_BOLL_UPPER", name: "碰触布林上轨" },
    { key: "PULLBACK", name: "良性缩量回踩" },
    { key: "VOLUME_SHRINK", name: "极度缩量清洗" },
    { key: "TOUCH_BOLL_MIDDLE", name: "触及布林中轨" },
    { key: "BOLL_MIDDLE_SUPPORT", name: "中轨企稳撑住" },
    { key: "STOP_FALLING_CANDLE", name: "收盘十字企稳" },
    { key: "VOLUME_BREAKOUT", name: "二次放量突破" },
  ];

  // hard_filters 状态
  const [hfMinAmount, setHfMinAmount] = useState(10000000);
  const [hfAllowSt, setHfAllowSt] = useState(false);
  const [hfMaxSuspended, setHfMaxSuspended] = useState(3);

  // required_events 状态
  const [requiredEvents, setRequiredEvents] = useState<string[]>([
    "TREND_UP", "TOUCH_BOLL_UPPER", "PULLBACK", "VOLUME_SHRINK",
    "TOUCH_BOLL_MIDDLE", "BOLL_MIDDLE_SUPPORT"
  ]);

  // default_backtest_config 状态
  const [btConfigHoldingPeriods, setBtConfigHoldingPeriods] = useState<number[]>([5, 10, 20]);
  const [btConfigBenchmark, setBtConfigBenchmark] = useState("sz399300");
  const [btConfigScoreThreshold, setBtConfigScoreThreshold] = useState(80.0);

  // 每日扫描
  const [runDate, setRunDate] = useState(() => {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  });
  const [scanResults, setScanResults] = useState<ScanResult[]>([]);
  const [selectedStock, setSelectedStock] = useState<ScanResult | null>(null);
  const [compareData, setCompareData] = useState<ComparePayload | null>(null);
  const [userComment, setUserComment] = useState('');
  const [feedbackVoted, setFeedbackVoted] = useState<Record<number, 'good_match' | 'bad_match'>>({});

  // 历史回测
  const [btStartDate, setBtStartDate] = useState('2026-03-01');
  const [btEndDate, setBtEndDate] = useState('2026-07-19');
  const [btScoreThreshold, setBtScoreThreshold] = useState(80);
  const [backtestResult, setBacktestResult] = useState<BacktestResult | null>(null);

  // 布林编排（Pattern）独立页
  const [bollPatterns, setBollPatterns] = useState<BollPatternMeta[]>([]);
  const [bollMatches, setBollMatches] = useState<BollPatternMatch[]>([]);
  const [bollMatchTotal, setBollMatchTotal] = useState(0);
  const [bollPatternFilter, setBollPatternFilter] = useState('');
  const [bollEndWithinDays, setBollEndWithinDays] = useState(3);
  const [bollWindowDays, setBollWindowDays] = useState(60);
  const [bollScanning, setBollScanning] = useState(false);
  const [bollScanProgress, setBollScanProgress] = useState<BollScanProgress | null>(null);
  const [bollLoading, setBollLoading] = useState(false);
  const [selectedBollMatch, setSelectedBollMatch] = useState<BollPatternMatch | null>(null);
  const [bollBars, setBollBars] = useState<Bar[]>([]);
  const [bollStateString, setBollStateString] = useState('');
  const [bollSettings, setBollSettings] = useState<{
    zone_thresholds: Record<string, [string | number, string | number]>;
    denoise_min_len: number;
  } | null>(null);

  // 个股走势（独立看盘页）
  const [stockSearchQuery, setStockSearchQuery] = useState('');
  const [stockSearchItems, setStockSearchItems] = useState<StockSearchItem[]>([]);
  const [stockSearchOpen, setStockSearchOpen] = useState(false);
  const [stockSearchLoading, setStockSearchLoading] = useState(false);
  const [selectedStockView, setSelectedStockView] = useState<StockSearchItem | null>(null);
  const [stockViewEndDate, setStockViewEndDate] = useState('');
  const [stockViewLookback, setStockViewLookback] = useState(120);
  const [stockViewBars, setStockViewBars] = useState<Bar[]>([]);
  const [stockViewLoading, setStockViewLoading] = useState(false);
  const [stockViewZoneOpen, setStockViewZoneOpen] = useState(false);
  const [stockViewZoneString, setStockViewZoneString] = useState('');
  const [stockViewZoneLoading, setStockViewZoneLoading] = useState(false);
  const stockViewReqKeyRef = useRef('');
  const stockSearchWrapRef = useRef<HTMLDivElement | null>(null);
  const stockViewEndDateRef = useRef<HTMLInputElement | null>(null);
  const bollAxisTooltipCacheRef = useRef<{ key: string; index: number; html: string } | null>(null);
  const stockViewAxisTooltipCacheRef = useRef<{ key: string; index: number; html: string } | null>(null);
  const bollAxisTooltipPosRef = useRef<{ key: string; index: number; pos: [number, number] } | null>(null);
  const stockViewAxisTooltipPosRef = useRef<{ key: string; index: number; pos: [number, number] } | null>(null);
  const [bollShowManage, setBollShowManage] = useState(false);
  const [bollEditId, setBollEditId] = useState<string | null>(null);
  const [bollForm, setBollForm] = useState({
    id: '',
    name: '',
    regex: '',
    min_total_days: 10,
    enabled: true,
    override_zone: false,
    override_denoise: false,
    bounds: { m: 0.35, h: 0.65, u: 0.95 },
    denoise: 0,
  });
  const [bollGlobalDenoise, setBollGlobalDenoise] = useState(0);
  const [bollGlobalBounds, setBollGlobalBounds] = useState({ m: 0.35, h: 0.65, u: 0.95 });
  const [bollGlobalBoundsInvalid, setBollGlobalBoundsInvalid] = useState(false);
  const [bollFormBoundsInvalid, setBollFormBoundsInvalid] = useState(false);

  // -----------------------------------------------------------------------------
  // 3. 通用辅助函数 (Helpers)
  // -----------------------------------------------------------------------------
  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  const getChineseEventName = (type: string) => {
    const names: Record<string, string> = {
      "TREND_UP": "上升趋势",
      "TOUCH_BOLL_UPPER": "碰触布林上轨",
      "PULLBACK": "良性缩量回踩",
      "VOLUME_SHRINK": "极度缩量清洗",
      "TOUCH_BOLL_MIDDLE": "触及布林中轨",
      "BOLL_MIDDLE_SUPPORT": "中轨企稳撑住",
      "STOP_FALLING_CANDLE": "收盘十字企稳",
      "VOLUME_BREAKOUT": "二次放量突破"
    };
    return names[type] || type;
  };

  // -----------------------------------------------------------------------------
  // 4. API 数据交互 (Side Effects)
  // -----------------------------------------------------------------------------
  // 4.0 拉取模板 Schema 元数据
  useEffect(() => {
    const fetchSchema = async () => {
      try {
        const res = await fetch(`${apiBase}/api/templates/schema`);
        const json = await res.json();
        if (json.success && json.data) {
          setTemplateSchema(json.data);
        }
      } catch (e) {
        console.error('拉取模板 Schema 失败:', e);
      }
    };
    fetchSchema();
  }, [apiBase]);

  // 4.1 拉取模板列表
  useEffect(() => {
    fetchTemplates();
  }, [apiBase]);

  const fetchTemplates = async () => {
    try {
      const res = await fetch(`${apiBase}/api/templates`);
      const json = await res.json();
      if (json.success && json.data.templates) {
        setTemplates(json.data.templates);
        if (json.data.templates.length > 0 && selectedTemplateId === null) {
          setSelectedTemplateId(json.data.templates[0].id);
        }
      } else {
        showToast('拉取特征模板失败：' + (json.error || '未知错误'));
      }
    } catch (e) {
      showToast('连接后端微服务失败，请确认 API 端口是否正确。');
    }
  };

  const handleSyncMarketData = async () => {
    setSyncing(true);
    showToast('🚀 正在发送全市场时序行情增量抓取指令，后端已接管...');
    try {
      const res = await fetch(`${apiBase}/api/jobs/sync-market-data`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          max_workers: maxWorkers,
          retry_limit: retryLimit,
          delay_min: delayMin,
          delay_max: delayMax
        })
      });
      const json = await res.json();
      if (json.success) {
        showToast('✅ 同步指令启动成功！后端已开始全速搬运数据，请查看 Terminal 日志监控！');
      } else {
        showToast('同步启动失败：' + (json.error || json.message));
      }
    } catch (e) {
      showToast('未连接上后端服务，同步指令发送超时。');
    } finally {
      setTimeout(() => setSyncing(false), 3000);
    }
  };

  const handleSyncTodayData = async () => {
    setSyncingToday(true);
    showToast('正在发送当日数据同步指令，后端已接管...');
    try {
      const res = await fetch(`${apiBase}/api/jobs/sync-today-data`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          max_workers: maxWorkers,
          retry_limit: retryLimit,
          delay_min: delayMin,
          delay_max: delayMax
        })
      });
      const json = await res.json();
      if (json.success) {
        showToast('当日数据同步指令启动成功！后端已开始快速抓取缺失数据，请查看 Terminal 日志监控！');
      } else {
        showToast('同步启动失败：' + (json.error || json.message));
      }
    } catch (e) {
      showToast('未连接上后端服务，同步指令发送超时。');
    } finally {
      setTimeout(() => setSyncingToday(false), 3000);
    }
  };

  // 4.2 当选中的模板 ID 改变时，拉取模板详情
  useEffect(() => {
    if (selectedTemplateId !== null) {
      fetchTemplateDetails(selectedTemplateId);
      setScanResults([]);
      setSelectedStock(null);
      setCompareData(null);
      setBacktestResult(null);
    }
  }, [selectedTemplateId, apiBase]);

  const fetchTemplateDetails = async (id: number) => {
    try {
      const res = await fetch(`${apiBase}/api/templates/${id}`);
      const json = await res.json();
      if (json.success) {
        setSelectedTemplateDetail(json.data);
        if (json.data.weights) {
          setWeightsMap(json.data.weights);
        }
        if (json.data.config) {
          if (json.data.name) setNewTplName(json.data.name);
          if (json.data.config.source_symbol) setNewTplSymbol(json.data.config.source_symbol);
          if (json.data.config.source_end) setNewTplEndDate(json.data.config.source_end);
          if (json.data.config.window_size) setNewTplWindowSize(json.data.config.window_size);

          if (json.data.config.hard_filters) {
            setHfMinAmount(json.data.config.hard_filters.min_amount_20d ?? 10000000);
            setHfAllowSt(json.data.config.hard_filters.allow_st ?? false);
            setHfMaxSuspended(json.data.config.hard_filters.max_suspended_days ?? 3);
          }
          if (json.data.config.required_events) {
            setRequiredEvents(json.data.config.required_events);
          }
          if (json.data.config.default_backtest_config) {
            const btc = json.data.config.default_backtest_config;
            setBtConfigHoldingPeriods(btc.holding_periods ?? [5, 10, 20]);
            setBtConfigBenchmark(btc.benchmark ?? "sz399300");
            setBtConfigScoreThreshold(btc.score_threshold ?? 80.0);
          }
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  // 4.3 触发一键全市场扫描大PK
  const handleRunMarketScan = async () => {
    if (selectedTemplateId === null) return;
    setLoading(true);
    showToast('🚀 正在激活全市场自动扫描哨兵，极速计算相似度中，请稍候...');
    try {
      const res = await fetch(`${apiBase}/api/search-runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          template_id: selectedTemplateId,
          run_date: runDate
        })
      });
      const json = await res.json();
      if (json.success) {
        showToast(`🎉 扫描推荐大PK成功！共搜寻到 ${json.data.results_count} 只神似个股！`);
        fetchScanResults();
      } else {
        showToast('每日扫描失败：' + json.error);
      }
    } catch (e) {
      showToast('请求超时，请检查后端运行状态。');
    } finally {
      setLoading(false);
    }
  };

  // 4.4 载入扫描持久化记录
  const fetchScanResults = async () => {
    if (selectedTemplateId === null) return;
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/search-runs/results?run_date=${runDate}&template_id=${selectedTemplateId}`);
      const json = await res.json();
      if (json.success) {
        setScanResults(json.data.results);
        if (json.data.results.length > 0) {
          handleSelectStockForCompare(json.data.results[0], false);
        } else {
          setSelectedStock(null);
          setCompareData(null);
        }
      } else {
        showToast('拉取历史扫描失败：' + json.error);
      }
    } catch (e) {
      showToast('加载扫描记录异常。');
    } finally {
      setLoading(false);
    }
  };

  // 4.4b 布林编排：列表 / 扫描 / 选中画图
  const fetchBollPatterns = async () => {
    try {
      const res = await fetch(`${apiBase}/api/boll-patterns?include_disabled=true`);
      const json = await res.json();
      if (json.success) {
        setBollPatterns(json.data.patterns || []);
        if (json.data.settings) {
          setBollSettings(json.data.settings);
          const zt = json.data.settings.zone_thresholds || {};
          setBollGlobalDenoise(Number(json.data.settings.denoise_min_len || 0));
          setBollGlobalBounds({
            m: Number(zt.M?.[0] ?? 0.35),
            h: Number(zt.H?.[0] ?? 0.65),
            u: Number(zt.U?.[0] ?? 0.95),
          });
        }
      }
    } catch {
      /* ignore */
    }
  };

  const parseZoneBounds = (
    zt?: Record<string, [string | number, string | number]> | null,
  ) => {
    const fallback = { m: bollGlobalBounds.m, h: bollGlobalBounds.h, u: bollGlobalBounds.u };
    if (!zt) return fallback;
    const num = (v: string | number | undefined, fb: number) => {
      if (v === undefined || v === null) return fb;
      if (v === '-inf' || v === 'inf') return fb;
      const n = Number(v);
      return Number.isFinite(n) ? n : fb;
    };
    return {
      m: num(zt.M?.[0] ?? zt.L?.[1], fallback.m),
      h: num(zt.H?.[0] ?? zt.M?.[1], fallback.h),
      u: num(zt.U?.[0] ?? zt.H?.[1], fallback.u),
    };
  };

  const buildZoneThresholds = (bounds: { m: number; h: number; u: number }) => ({
    L: ['-inf', bounds.m],
    M: [bounds.m, bounds.h],
    H: [bounds.h, bounds.u],
    U: [bounds.u, 'inf'],
  });

  const openNewBollForm = () => {
    setBollEditId(null);
    setBollForm({
      id: '',
      name: '',
      regex: 'L+M{3,}H*U+H*M{3,}',
      min_total_days: 10,
      enabled: true,
      override_zone: false,
      override_denoise: false,
      bounds: { ...bollGlobalBounds },
      denoise: bollGlobalDenoise,
    });
    setBollShowManage(true);
    setBollFormBoundsInvalid(false);
  };

  const openEditBollForm = (p: BollPatternMeta) => {
    setBollEditId(p.id);
    const hasZoneOverride = p.zone_thresholds != null;
    const hasDenoiseOverride = p.denoise_min_len != null;
    setBollForm({
      id: p.id,
      name: p.name,
      regex: p.regex || '',
      min_total_days: p.min_total_days ?? 0,
      enabled: p.enabled,
      override_zone: hasZoneOverride,
      override_denoise: hasDenoiseOverride,
      bounds: parseZoneBounds(hasZoneOverride ? p.zone_thresholds : p.effective?.zone_thresholds),
      denoise: hasDenoiseOverride
        ? Number(p.denoise_min_len)
        : Number(p.effective?.denoise_min_len ?? bollGlobalDenoise),
    });
    setBollShowManage(true);
    setBollFormBoundsInvalid(false);
  };

  const handleSaveBollPattern = async () => {
    try {
      if (bollForm.override_zone && !isZoneBoundsOrdered(bollForm.bounds)) {
        setBollFormBoundsInvalid(true);
        showToast('本编排分区边界须满足 m < h < u');
        return;
      }
      setBollFormBoundsInvalid(false);

      const zonePayload = bollForm.override_zone
        ? buildZoneThresholds(bollForm.bounds)
        : null;
      const denoisePayload = bollForm.override_denoise ? bollForm.denoise : null;

      if (bollEditId) {
        const body: Record<string, unknown> = {
          name: bollForm.name,
          regex: bollForm.regex,
          min_total_days: bollForm.min_total_days,
          enabled: bollForm.enabled,
          clear_zone_override: !bollForm.override_zone,
          clear_denoise_override: !bollForm.override_denoise,
        };
        if (bollForm.override_zone) body.zone_thresholds = zonePayload;
        if (bollForm.override_denoise) body.denoise_min_len = denoisePayload;

        const res = await fetch(`${apiBase}/api/boll-patterns/${bollEditId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const json = await res.json();
        if (!json.success) {
          showToast('更新编排失败：' + (json.error || ''));
          return;
        }
        showToast('编排已更新');
      } else {
        if (!bollForm.id.trim()) {
          showToast('请填写编排 id');
          return;
        }
        const res = await fetch(`${apiBase}/api/boll-patterns`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            id: bollForm.id,
            name: bollForm.name,
            regex: bollForm.regex,
            min_total_days: bollForm.min_total_days,
            enabled: bollForm.enabled,
            zone_thresholds: zonePayload,
            denoise_min_len: denoisePayload,
          }),
        });
        const json = await res.json();
        if (!json.success) {
          showToast('创建编排失败：' + (json.error || ''));
          return;
        }
        showToast('编排已创建');
      }
      setBollShowManage(false);
      void fetchBollPatterns();
    } catch {
      showToast('保存编排失败');
    }
  };

  const handleDisableBollPattern = async (id: string) => {
    try {
      const res = await fetch(`${apiBase}/api/boll-patterns/${id}/disable`, { method: 'POST' });
      const json = await res.json();
      if (!json.success) {
        showToast('禁用失败：' + (json.error || ''));
        return;
      }
      showToast('已软禁用编排（历史命中保留）');
      void fetchBollPatterns();
    } catch {
      showToast('禁用编排异常');
    }
  };

  const handleToggleBollEnabled = async (p: BollPatternMeta, enabled: boolean) => {
    try {
      const res = await fetch(`${apiBase}/api/boll-patterns/${p.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      const json = await res.json();
      if (!json.success) {
        showToast('更新启用状态失败：' + (json.error || ''));
        return;
      }
      void fetchBollPatterns();
    } catch {
      showToast('更新启用状态异常');
    }
  };

  const handleSaveBollSettings = async () => {
    try {
      if (!isZoneBoundsOrdered(bollGlobalBounds)) {
        setBollGlobalBoundsInvalid(true);
        showToast('全局分区边界须满足 m < h < u');
        return;
      }
      setBollGlobalBoundsInvalid(false);
      const zone_thresholds = buildZoneThresholds(bollGlobalBounds);
      const res = await fetch(`${apiBase}/api/boll-pattern-settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          zone_thresholds,
          denoise_min_len: bollGlobalDenoise,
        }),
      });
      const json = await res.json();
      if (!json.success) {
        showToast('保存全局尺子失败：' + (json.error || ''));
        return;
      }
      showToast('全局尺子已保存（下次扫描生效）');
      void fetchBollPatterns();
    } catch {
      showToast('保存全局尺子异常');
    }
  };

  const fetchBollMatches = async () => {
    setBollLoading(true);
    try {
      const qs = new URLSearchParams({
        end_within_days: String(bollEndWithinDays),
        order_by: 'score',
        limit: '100',
      });
      if (bollPatternFilter) qs.set('pattern_id', bollPatternFilter);
      const res = await fetch(`${apiBase}/api/boll-pattern-matches?${qs}`);
      const json = await res.json();
      if (json.success) {
        const items: BollPatternMatch[] = json.data.items || [];
        setBollMatches(items);
        setBollMatchTotal(json.data.total || 0);
        if (items.length > 0) {
          void handleSelectBollMatch(items[0]);
        } else {
          setSelectedBollMatch(null);
          setBollBars([]);
          setBollStateString('');
        }
      } else {
        showToast('拉取编排命中失败：' + (json.error || ''));
      }
    } catch {
      showToast('拉取编排命中异常，请检查后端。');
    } finally {
      setBollLoading(false);
    }
  };

  const fetchBollScanStatus = async (): Promise<BollScanProgress | null> => {
    try {
      const res = await fetch(`${apiBase}/api/jobs/scan-boll-patterns/status`);
      const json = await res.json();
      if (json.success && json.data) {
        const prog = json.data as BollScanProgress;
        setBollScanProgress(prog);
        return prog;
      }
    } catch {
      /* 轮询失败不打断 UI */
    }
    return null;
  };

  const handleTriggerBollScan = async () => {
    setBollScanning(true);
    setBollScanProgress({
      running: true,
      phase: 'preparing',
      current: 0,
      total: 0,
      scanned: 0,
      skipped: 0,
      errors: 0,
      matches: 0,
      percent: 0,
      window_days: bollWindowDays,
      scan_date: null,
      current_code: null,
      message: '正在启动扫描…',
      started_at: null,
      finished_at: null,
      summary: null,
      error: null,
    });
    showToast(`布林编排扫描已启动（window_days=${bollWindowDays}）...`);
    try {
      const res = await fetch(`${apiBase}/api/jobs/scan-boll-patterns`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ window_days: bollWindowDays }),
      });
      const json = await res.json();
      if (json.success) {
        showToast(json.message || '扫描已在后台运行');
        // 保持 bollScanning=true，由进度轮询在结束后关闭
      } else {
        showToast('启动编排扫描失败：' + (json.error || ''));
        setBollScanning(false);
        setBollScanProgress(null);
      }
    } catch {
      showToast('无法连接后端启动编排扫描');
      setBollScanning(false);
      setBollScanProgress(null);
    }
  };

  const handleSelectBollMatch = async (m: BollPatternMatch) => {
    setSelectedBollMatch(m);
    setBollLoading(true);
    try {
      const [barsRes, stateRes] = await Promise.all([
        fetch(`${apiBase}/api/stocks/${m.code}/bars?end_date=${m.end_date}&lookback_days=120`),
        fetch(`${apiBase}/api/stocks/${m.code}/boll-states?limit=60`),
      ]);
      const barsJson = await barsRes.json();
      const stateJson = await stateRes.json();
      if (barsJson.success) {
        setBollBars(barsJson.data.bars || []);
      } else {
        setBollBars([]);
        showToast('加载 K 线失败：' + (barsJson.error || ''));
      }
      if (stateJson.success) {
        setBollStateString(stateJson.data.state_string || '');
      }
    } catch {
      showToast('加载编排个股图表异常');
      setBollBars([]);
    } finally {
      setBollLoading(false);
    }
  };

  const loadStockViewBars = async (
    code: string,
    endDate: string | undefined,
    lookback: number,
  ) => {
    const lb = Math.min(500, Math.max(30, lookback || 120));
    const key = `${code}|${endDate || 'latest'}|${lb}`;
    if (stockViewReqKeyRef.current === key) return;
    stockViewReqKeyRef.current = key;
    setStockViewLoading(true);
    try {
      const qs = new URLSearchParams();
      qs.set('lookback_days', String(lb));
      if (endDate) qs.set('end_date', endDate);
      const res = await fetch(`${apiBase}/api/stocks/${code}/bars?${qs.toString()}`);
      const json = await res.json();
      if (json.success) {
        const resolvedEnd = json.data?.end_date || endDate || '';
        if (!endDate && resolvedEnd) {
          stockViewReqKeyRef.current = `${code}|${resolvedEnd}|${lb}`;
          setStockViewEndDate(resolvedEnd);
        }
        setStockViewBars(json.data.bars || []);
      } else {
        setStockViewBars([]);
        showToast('加载 K 线失败：' + (json.error || ''));
      }
    } catch {
      setStockViewBars([]);
      showToast('加载个股走势异常');
    } finally {
      setStockViewLoading(false);
    }
  };

  const handleSelectStockView = (item: StockSearchItem) => {
    setSelectedStockView(item);
    setStockSearchQuery(`${item.code.toUpperCase()} ${item.name || ''}`.trim());
    setStockSearchOpen(false);
    setStockSearchItems([]);
    setStockViewZoneOpen(false);
    setStockViewZoneString('');
    stockViewReqKeyRef.current = '';
    setStockViewEndDate('');
  };

  // 4.5 点击股票拉取核心同屏对齐比对数据
  const handleSelectStockForCompare = async (stock: ScanResult, triggerDrawer: boolean = false) => {
    setSelectedStock(stock);
    setCompareLoading(true);
    setChartView('compare');
    if (triggerDrawer) {
      setDrawerOpen(true);
    }
    try {
      const res = await fetch(`${apiBase}/api/compare/template/${selectedTemplateId}/stock/${stock.code}?end_date=${runDate}`);
      const json = await res.json();
      if (json.success) {
        setCompareData(json.data);
        setUserComment('');
      } else {
        showToast('加载同屏比对失败：' + json.error);
        setCompareData(null);
      }
    } catch (e) {
      showToast('获取个股形态对齐路径异常。');
      setCompareData(null);
    } finally {
      setCompareLoading(false);
    }
  };

  // 4.6 提交人工正负标注反馈
  const handleSubmitFeedback = async (label: 'good_match' | 'bad_match') => {
    if (!selectedStock || selectedTemplateId === null) return;
    try {
      const res = await fetch(`${apiBase}/api/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          result_id: selectedStock.id,
          label: label,
          comment: userComment || (label === 'good_match' ? '好匹配' : '不像'),
          learning_rate: learningRate
        })
      });
      const json = await res.json();
      if (json.success) {
        setFeedbackVoted(prev => ({ ...prev, [selectedStock.id]: label }));
        showToast(`👍 反馈提交成功！模板特征权重已自更新归一化！`);
        fetchTemplateDetails(selectedTemplateId);
      } else {
        showToast('提交反馈失败：' + json.error);
      }
    } catch (e) {
      showToast('反馈网络交互失败。');
    }
  };

  // 4.7 发起形态滚动无偏回测
  const handleRunBacktest = async () => {
    if (selectedTemplateId === null) return;
    setLoading(true);
    showToast('👉 正在暖机预加载并开启 60 日滑动滚动仿真回测，100% 无未来函数，请静待 10~20 秒...');
    try {
      const res = await fetch(`${apiBase}/api/backtests`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          template_id: selectedTemplateId,
          start_date: btStartDate,
          end_date: btEndDate,
          score_threshold: btScoreThreshold
        })
      });
      const json = await res.json();
      if (json.success) {
        setBacktestResult(json.data);
        showToast(`📈 回测完成！总搜寻到买入信号 ${json.data.summary.total_signals} 个，科学大捷！`);
      } else {
        showToast('回测失败：' + json.error);
      }
    } catch (e) {
      showToast('回测计算超时或异常，请检查后台日志。');
    } finally {
      setLoading(false);
    }
  };

  // 4.11 【核心黑客机制】特征权重滑块拉动 L1 物理自平衡算法
  const handleWeightSliderChange = (featureKey: string, newValue: number) => {
    const targetVal = Math.max(0.01, Math.min(0.99, newValue));
    const otherKeys = Object.keys(weightsMap).filter(k => k !== featureKey);
    const otherSum = otherKeys.reduce((sum, k) => sum + weightsMap[k], 0);

    const nextWeights = { ...weightsMap };
    nextWeights[featureKey] = targetVal;
    const remaining = 1.0 - targetVal;

    if (otherSum > 0) {
      otherKeys.forEach(k => {
        nextWeights[k] = Number(((weightsMap[k] / otherSum) * remaining).toFixed(4));
      });
    } else {
      otherKeys.forEach(k => {
        nextWeights[k] = Number((remaining / otherKeys.length).toFixed(4));
      });
    }

    const finalSum = Object.values(nextWeights).reduce((sum, v) => sum + v, 0);
    const diff = 1.0 - finalSum;
    if (Math.abs(diff) > 0.0001) {
      const maxKey = Object.keys(nextWeights).reduce((a, b) => nextWeights[a] > nextWeights[b] ? a : b);
      nextWeights[maxKey] = Number((nextWeights[maxKey] + diff).toFixed(4));
    }

    setWeightsMap(nextWeights);
  };

  // 4.12 股票代码输入实时查询中文名
  useEffect(() => {
    const resolveName = async () => {
      const cleanSym = newTplSymbol.toLowerCase().trim();
      if (cleanSym.length !== 8) {
        setResolvedStockName('');
        return;
      }
      try {
        const res = await fetch(`${apiBase}/api/stocks/${cleanSym}`);
        const json = await res.json();
        if (json.success && json.data) {
          setResolvedStockName(`${json.data.name} (${json.data.board})`);
        } else {
          setResolvedStockName('⚠️ 未查到该个股代码');
        }
      } catch (e) {
        setResolvedStockName('');
      }
    };
    resolveName();
  }, [newTplSymbol, apiBase]);

  // 4.13 一键注册全新形态匹配标尺
  const handleCreateTemplate = async () => {
    if (!newTplName.trim()) {
      showToast('⚠️ 注册失败：请输入形态模板名称！');
      return;
    }
    const cleanSym = newTplSymbol.toLowerCase().trim();
    if (cleanSym.length !== 8) {
      showToast('⚠️ 注册失败：请输入 8 位带市场前缀的代码（如 sz000002）！');
      return;
    }

    setLoading(true);
    try {
      const payload = {
        name: newTplName,
        type: 'historical',
        config: {
          window_size: newTplWindowSize,
          source_symbol: cleanSym,
          source_start: "2026-01-01",
          source_end: newTplEndDate,
          hard_filters: {
            min_amount_20d: hfMinAmount,
            allow_st: hfAllowSt,
            max_suspended_days: hfMaxSuspended,
          },
          required_events: requiredEvents,
          default_backtest_config: {
            holding_periods: btConfigHoldingPeriods,
            benchmark: btConfigBenchmark,
            score_threshold: btConfigScoreThreshold,
          },
        },
        weights: weightsMap
      };

      const res = await fetch(`${apiBase}/api/templates`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const json = await res.json();
      if (json.success) {
        showToast(`🎉 形态标尺 [${newTplName}] 一键注册点火成功！`);
        await fetchTemplates();
        setNewTplName('');
        setNewTplSymbol('');
        setNewTplEndDate('2026-07-19');
        setNewTplWindowSize(60);
        setHfMinAmount(10000000);
        setHfAllowSt(false);
        setHfMaxSuspended(3);
        setRequiredEvents(["TREND_UP", "TOUCH_BOLL_UPPER", "PULLBACK", "VOLUME_SHRINK", "TOUCH_BOLL_MIDDLE", "BOLL_MIDDLE_SUPPORT"]);
        setBtConfigHoldingPeriods([5, 10, 20]);
        setBtConfigBenchmark("sz399300");
        setBtConfigScoreThreshold(80.0);
        setResolvedStockName('');
      } else {
        showToast('创建模板失败：' + json.error);
      }
    } catch (e) {
      showToast('模板创建网络交互超时。');
    } finally {
      setLoading(false);
    }
  };

  // 4.14 覆盖更新当前选中模板的全部参数
  const handleUpdateTemplate = async () => {
    if (!selectedTemplateId || selectedTemplateDetail === null) return;
    setLoading(true);
    try {
      const cleanSym = newTplSymbol.toLowerCase().trim();
      const payload = {
        name: newTplName.trim() || selectedTemplateDetail.name,
        type: selectedTemplateDetail.type,
        config: {
          window_size: newTplWindowSize,
          source_symbol: cleanSym || selectedTemplateDetail.config?.source_symbol,
          source_start: selectedTemplateDetail.config?.source_start || "2026-01-01",
          source_end: newTplEndDate || selectedTemplateDetail.config?.source_end,
          hard_filters: {
            min_amount_20d: hfMinAmount,
            allow_st: hfAllowSt,
            max_suspended_days: hfMaxSuspended,
          },
          required_events: requiredEvents,
          default_backtest_config: {
            holding_periods: btConfigHoldingPeriods,
            benchmark: btConfigBenchmark,
            score_threshold: btConfigScoreThreshold,
          },
        },
        weights: weightsMap
      };

      const res = await fetch(`${apiBase}/api/templates/${selectedTemplateId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const json = await res.json();
      if (json.success) {
        showToast(`💾 成功覆盖更新形态模板 [${selectedTemplateDetail.name}] 全部参数！`);
        await fetchTemplateDetails(selectedTemplateId);
      } else {
        showToast('更新模板失败：' + json.error);
      }
    } catch (e) {
      showToast('模板权重保存超时。');
    } finally {
      setLoading(false);
    }
  };

  // 4.15 一键物理清除该形态
  const handleDeleteTemplate = async () => {
    if (selectedTemplateId === null || !selectedTemplateDetail) return;

    const confirmDel = window.confirm(`🚨 物理拔线警告：\n确定要一键销毁形态模板 [${selectedTemplateDetail.name}] 吗？\n删除后将不可恢复，且关联的扫描历史也会自动幂等清理！`);
    if (!confirmDel) return;

    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/templates/${selectedTemplateId}`, {
        method: 'DELETE'
      });
      const json = await res.json();
      if (json.success) {
        showToast(`🗑️ 形态标尺 [${selectedTemplateDetail.name}] 物理销毁及大账清扫完成！`);
        setSelectedTemplateId(null);
        setSelectedTemplateDetail(null);
        await fetchTemplates();
      } else {
        showToast('物理销毁失败：' + json.error);
      }
    } catch (e) {
      showToast('物理销毁接口交互超时。');
    } finally {
      setLoading(false);
    }
  };

  // 4.16 渲染全新、高扩展可视化形态模板前台
  const renderTemplatesTab = () => {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>

        {/* 顶部：模板大卡片 Grid 列表 */}
        <div className="templates-grid">
          {templates.map(tpl => (
            <div
              key={tpl.id}
              className={`tpl-item-card ${selectedTemplateId === tpl.id ? 'active' : ''}`}
              onClick={() => setSelectedTemplateId(tpl.id)}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span className={`tpl-badge ${tpl.type}`}>{tpl.type === 'historical' ? '历史切片母体' : '抽象逻辑'}</span>
                <span style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)' }}>ID: {tpl.id}</span>
              </div>
              <h3 style={{ fontSize: '1.05rem', fontWeight: '750', marginTop: '0.4rem', color: '#fff' }}>{tpl.name}</h3>
              <p style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)' }}>
                {tpl.config?.source_symbol ? `锚定: ${tpl.config.source_symbol.toUpperCase()}` : '未锚定个股'}
              </p>
            </div>
          ))}
        </div>

        {/* 中部：创建标尺与管理卡片 (Grid 1:1) */}
        <div className="settings-container">

          {/* 左卡：注册全新形态标尺 */}
          <div className="settings-left-card">
            <div className="settings-section-title">➕ 注册全新形态对比标尺 (Add New Pattern)</div>

            <div className="settings-form">
              <div className="form-item">
                <label>形态模板名称 (name)</label>
                <input
                  type="text"
                  value={newTplName}
                  onChange={(e) => setNewTplName(e.target.value)}
                  placeholder="如：回踩中轨缩量专属形态"
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                <div className="form-item">
                  <label>锚定母体代码 (source_symbol)</label>
                  <input
                    type="text"
                    value={newTplSymbol}
                    onChange={(e) => setNewTplSymbol(e.target.value)}
                    placeholder="如：sz000002"
                  />
                  {resolvedStockName && (
                    <span className="form-item-tip" style={{ color: resolvedStockName.startsWith('⚠️') ? '#fca5a5' : 'var(--color-primary)', fontWeight: 'bold' }}>
                      ℹ️ {resolvedStockName}
                    </span>
                  )}
                </div>
                <div className="form-item">
                  <label>历史截止日期 (source_end)</label>
                  <input
                    type="date"
                    value={newTplEndDate}
                    onChange={(e) => setNewTplEndDate(e.target.value)}
                  />
                </div>
              </div>

              <div className="form-item">
                <label>计算滑动窗口步长 (window_size): <b style={{ color: 'var(--color-primary)' }}>{newTplWindowSize}天</b></label>
                <input
                  type="range"
                  value={newTplWindowSize}
                  onChange={(e) => setNewTplWindowSize(Number(e.target.value))}
                  min={15}
                  max={120}
                  style={{ height: '6px', background: 'rgba(255,255,255,0.05)', borderRadius: '3px', outline: 'none', WebkitAppearance: 'none' }}
                />
              </div>

              {/* 📡 必需事件序列 */}
              <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '0.8rem', marginTop: '0.4rem' }}>
                <span style={{ fontSize: '0.8rem', fontWeight: '700', color: 'var(--color-text-main)', display: 'block', marginBottom: '0.6rem' }}>
                  📡 必需事件序列 (required_events)
                </span>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                  {(templateSchema?.event_types || defaultEventTypes).map((evt: any) => (
                    <label key={evt.key} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.85rem' }}>
                      <input
                        type="checkbox"
                        checked={requiredEvents.includes(evt.key)}
                        onChange={(e) => {
                          setRequiredEvents(prev =>
                            e.target.checked
                              ? [...prev, evt.key]
                              : prev.filter(x => x !== evt.key)
                          );
                        }}
                      />
                      {evt.name}
                    </label>
                  ))}
                </div>
              </div>

              {/* 🛡️ 硬性过滤条件 */}
              <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '0.8rem', marginTop: '0.4rem' }}>
                <span style={{ fontSize: '0.8rem', fontWeight: '700', color: 'var(--color-text-main)', display: 'block', marginBottom: '0.6rem' }}>
                  🛡️ 硬性过滤条件 (hard_filters)
                </span>
                <div className="settings-form">
                  <div className="form-item">
                    <label>20日均成交额最低门槛 (min_amount_20d)</label>
                    <input
                      type="number"
                      value={hfMinAmount}
                      onChange={(e) => setHfMinAmount(Number(e.target.value))}
                      min={0}
                      step={1000000}
                      style={{ background: '#0a0d16', border: '1px solid var(--border-color)', borderRadius: '6px', color: '#fff', padding: '0.45rem 0.8rem', fontSize: '0.85rem', outline: 'none' }}
                    />
                    <span className="form-item-tip">单位：元。低于此值视为僵尸股剔除。</span>
                  </div>
                  <div className="form-item" style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                    <input
                      type="checkbox"
                      checked={hfAllowSt}
                      onChange={(e) => setHfAllowSt(e.target.checked)}
                      style={{ width: 'auto', margin: 0 }}
                    />
                    <label style={{ fontSize: '0.85rem' }}>允许 ST / 退市整理股 (allow_st)</label>
                    <span className="form-item-tip">默认关闭，绝缘 ST 股。</span>
                  </div>
                  <div className="form-item">
                    <label>最大允许停牌天数 (max_suspended_days)</label>
                    <input
                      type="number"
                      value={hfMaxSuspended}
                      onChange={(e) => setHfMaxSuspended(Number(e.target.value))}
                      min={0}
                      max={30}
                      style={{ background: '#0a0d16', border: '1px solid var(--border-color)', borderRadius: '6px', color: '#fff', padding: '0.45rem 0.8rem', fontSize: '0.85rem', outline: 'none' }}
                    />
                  </div>
                </div>
              </div>

              {/* 📈 回测默认配置 */}
              <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '0.8rem', marginTop: '0.4rem' }}>
                <span style={{ fontSize: '0.8rem', fontWeight: '700', color: 'var(--color-text-main)', display: 'block', marginBottom: '0.6rem' }}>
                  📈 回测默认配置 (default_backtest_config)
                </span>
                <div className="settings-form">
                  <div className="form-item">
                    <label>持股周期 (holding_periods)</label>
                    <input
                      type="text"
                      value={btConfigHoldingPeriods.join(', ')}
                      readOnly
                      style={{ background: '#0a0d16', border: '1px solid var(--border-color)', borderRadius: '6px', color: '#94a3b8', padding: '0.45rem 0.8rem', fontSize: '0.85rem', outline: 'none' }}
                    />
                    <span className="form-item-tip">当前: {btConfigHoldingPeriods.join(', ')} 天。在回测 Tab 可覆盖。</span>
                  </div>
                  <div className="form-item">
                    <label>业绩基准 (benchmark)</label>
                    <input
                      type="text"
                      value={btConfigBenchmark}
                      onChange={(e) => setBtConfigBenchmark(e.target.value)}
                      placeholder="如 sz399300"
                      style={{ background: '#0a0d16', border: '1px solid var(--border-color)', borderRadius: '6px', color: '#fff', padding: '0.45rem 0.8rem', fontSize: '0.85rem', outline: 'none' }}
                    />
                  </div>
                  <div className="form-item">
                    <label>默认买入阈值 (score_threshold)</label>
                    <input
                      type="number"
                      value={btConfigScoreThreshold}
                      onChange={(e) => setBtConfigScoreThreshold(Number(e.target.value))}
                      min={0}
                      max={100}
                      step={1}
                      style={{ background: '#0a0d16', border: '1px solid var(--border-color)', borderRadius: '6px', color: '#fff', padding: '0.45rem 0.8rem', fontSize: '0.85rem', outline: 'none' }}
                    />
                  </div>
                </div>
              </div>

              {/* 7 大核心权重手工微调 (自平衡) */}
              <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '0.8rem', marginTop: '0.4rem' }}>
                <span style={{ fontSize: '0.8rem', fontWeight: '700', color: 'var(--color-text-main)', display: 'block', marginBottom: '0.6rem' }}>
                  🎯 7 大特征维度权重手动微调 (L1 自动 100% 守恒配平)
                </span>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
                  {Object.entries(weightsMap).map(([feature, val]) => (
                    <div key={feature} style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.72rem', color: 'var(--color-text-muted)', fontWeight: 'bold' }}>
                        <span>
                          {feature === 'close_norm' && '📈 归一化首日价格对齐 (close_norm)'}
                          {feature === 'boll_mid_dist' && '🧭 距离布林中轨偏离度 (boll_mid_dist)'}
                          {feature === 'volume_ratio_20' && '📊 20日均量成交缩量比 (volume_ratio_20)'}
                          {feature === 'close_position' && '🕯️ K线收盘落脚点位置 (close_position)'}
                          {feature === 'return_5d' && '⚡ 5日局部变动收益排布 (return_5d)'}
                          {feature === 'range_ratio' && '🏹 滑动价格最高最低振幅 (range_ratio)'}
                          {feature === 'atr_ratio' && '🌊 真实波动率 ATR 振荡比 (atr_ratio)'}
                        </span>
                        <span style={{ color: 'var(--color-primary)', fontFamily: 'monospace' }}>{(val * 100).toFixed(2)}%</span>
                      </div>
                      <input
                        type="range"
                        value={val}
                        onChange={(e) => handleWeightSliderChange(feature, Number(e.target.value))}
                        step={0.01}
                        min={0.01}
                        max={0.99}
                        style={{ height: '4px', background: 'rgba(255,255,255,0.05)', borderRadius: '2px', outline: 'none', WebkitAppearance: 'none' }}
                      />
                    </div>
                  ))}
                </div>
              </div>

              <button
                className="btn-primary"
                style={{ marginTop: '0.5rem', background: 'linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%)' }}
                onClick={handleCreateTemplate}
              >
                <Sparkles size={14} /> 一键点火注册全新形态标尺
              </button>
            </div>
          </div>

          {/* 右卡：覆写更新与删除已有标尺 */}
          <div className="settings-right-card">
            <div className="settings-section-title">⚙️ 覆写微调与物理管护 (Update & Delete)</div>

            {selectedTemplateDetail ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1.2rem', height: '100%', justifyContent: 'space-between' }}>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
                  <div style={{ background: 'rgba(255,255,255,0.01)', padding: '1rem', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
                    <h4 style={{ fontSize: '0.9rem', color: '#fff', fontWeight: 'bold', marginBottom: '0.4rem' }}>
                      📋 当前选中形态：<b style={{ color: 'var(--color-primary)' }}>{selectedTemplateDetail.name}</b>
                    </h4>
                    <p style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)', lineHeight: '1.5' }}>
                      该模板当前锚定股票代码为 <b>{selectedTemplateDetail.config?.source_symbol?.toUpperCase()}</b>，
                      历史截止交易日为 <b>{selectedTemplateDetail.config?.source_end}</b>，
                      形态滑窗为 <b>{selectedTemplateDetail.config?.window_size}</b> 天。
                    </p>
                  </div>

                  {/* 实时雷达图 */}
                  <div style={{ height: '300px', width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <ReactECharts
                      option={getRadarOption()}
                      style={{ height: '100%', width: '100%' }}
                    />
                  </div>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem', borderTop: '1px solid var(--border-color)', paddingTop: '1rem' }}>
                  <button
                    className="btn-primary"
                    style={{ background: 'linear-gradient(135deg, #10b981 0%, #059669 100%)', boxShadow: '0 4px 12px rgba(16, 185, 129, 0.2)' }}
                    onClick={handleUpdateTemplate}
                  >
                    💾 覆写并保存当前模板全部参数
                  </button>
                  <button
                    className="btn-primary"
                    style={{ background: 'linear-gradient(135deg, #ef4444 0%, #dc2626 100%)', boxShadow: '0 4px 12px rgba(239, 68, 68, 0.2)' }}
                    onClick={handleDeleteTemplate}
                  >
                    🗑️ 一键物理拔线清除此形态标尺
                  </button>
                </div>

              </div>
            ) : (
              <div className="empty-wrapper" style={{ height: '100%', justifyContent: 'center', margin: 'auto' }}>
                <Settings size={32} className="spinner" style={{ animationDuration: '4s' }} />
                <p style={{ fontSize: '0.85rem' }}>朔哥哥，请选择上方大列表中的任何一个已有模板卡片，即可激活该形态的覆写与物理删除管护面板。</p>
              </div>
            )}
          </div>

        </div>

      </div>
    );
  };

  // 4.8 保存系统全局配置项到本地 localStorage
  const handleSaveConfigs = () => {
    localStorage.setItem('window_size', String(windowSize));
    localStorage.setItem('max_workers', String(maxWorkers));
    localStorage.setItem('delay_min', String(delayMin));
    localStorage.setItem('delay_max', String(delayMax));
    localStorage.setItem('retry_limit', String(retryLimit));
    localStorage.setItem('learning_rate', String(learningRate));
    showToast('⚙️ 系统配置参数一键持久化成功！已应用到全模块。');
  };

  // 4.9 数据库宏观事实与实时日志轮询器 (仅在 settings Tab 被激活时工作)
  useEffect(() => {
    if (activeTab !== 'settings') return;

    const fetchStats = async () => {
      try {
        const res = await fetch(`${apiBase}/api/stats`);
        const json = await res.json();
        if (json.success) {
          setDbStats(json.data);
        }
      } catch (e) {
        console.error('获取数据库宏观统计失败:', e);
      }
    };

    const fetchLogs = async () => {
      try {
        const res = await fetch(`${apiBase}/api/logs?lines=25`);
        const json = await res.json();
        if (json.success && json.data.logs) {
          setLiveLogs(json.data.logs);
        }
      } catch (e) {
        console.error('获取实时日志事实失败:', e);
      }
    };

    fetchStats();
    fetchLogs();

    const interval = setInterval(() => {
      fetchStats();
      fetchLogs();
    }, syncing ? 1500 : 3000);

    return () => clearInterval(interval);
  }, [activeTab, syncing, syncingToday, apiBase]);

  useEffect(() => {
    if (activeTab !== 'boll_pattern') return;
    void fetchBollPatterns();
    void fetchBollMatches();
    // 进入页时若后台已在扫，恢复进度展示
    void (async () => {
      const prog = await fetchBollScanStatus();
      if (prog?.running || prog?.phase === 'preparing' || prog?.phase === 'scanning') {
        setBollScanning(true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, apiBase, bollEndWithinDays, bollPatternFilter]);

  // 个股走势：模糊搜索联想
  useEffect(() => {
    if (activeTab !== 'stock_view') return;
    const q = stockSearchQuery.trim();
    if (q.length < 1) {
      setStockSearchItems([]);
      setStockSearchOpen(false);
      return;
    }
    // 已选中且输入框仍是「代码 名称」展示时，不重复搜
    if (
      selectedStockView &&
      q === `${selectedStockView.code.toUpperCase()} ${selectedStockView.name || ''}`.trim()
    ) {
      return;
    }
    let cancelled = false;
    const t = setTimeout(async () => {
      setStockSearchLoading(true);
      try {
        const res = await fetch(
          `${apiBase}/api/stocks/search?q=${encodeURIComponent(q)}&limit=10`,
        );
        const json = await res.json();
        if (cancelled) return;
        if (json.success) {
          setStockSearchItems(json.data?.items || []);
          setStockSearchOpen(true);
        } else {
          setStockSearchItems([]);
        }
      } catch {
        if (!cancelled) setStockSearchItems([]);
      } finally {
        if (!cancelled) setStockSearchLoading(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [activeTab, apiBase, stockSearchQuery, selectedStockView]);

  // 个股走势：窗口参数变更防抖拉图
  useEffect(() => {
    if (activeTab !== 'stock_view') return;
    if (!selectedStockView) return;
    if (stockViewLookback < 30 || stockViewLookback > 500) return;
    const t = setTimeout(() => {
      void loadStockViewBars(
        selectedStockView.code,
        stockViewEndDate || undefined,
        stockViewLookback,
      );
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, selectedStockView?.code, stockViewEndDate, stockViewLookback, apiBase]);

  // 个股走势：展开 zone 时再请求
  useEffect(() => {
    if (activeTab !== 'stock_view' || !stockViewZoneOpen || !selectedStockView) return;
    let cancelled = false;
    setStockViewZoneLoading(true);
    void (async () => {
      try {
        const res = await fetch(
          `${apiBase}/api/stocks/${selectedStockView.code}/boll-states?limit=60`,
        );
        const json = await res.json();
        if (cancelled) return;
        if (json.success) {
          setStockViewZoneString(json.data?.state_string || '');
        } else {
          setStockViewZoneString('');
          showToast('加载 zone 失败：' + (json.error || ''));
        }
      } catch {
        if (!cancelled) {
          setStockViewZoneString('');
          showToast('加载 zone 异常');
        }
      } finally {
        if (!cancelled) setStockViewZoneLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, stockViewZoneOpen, selectedStockView?.code, apiBase]);

  // 点击外部关闭搜索下拉
  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (!stockSearchWrapRef.current) return;
      if (!stockSearchWrapRef.current.contains(e.target as Node)) {
        setStockSearchOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, []);

  // 布林编排扫描进度轮询
  useEffect(() => {
    if (!bollScanning) return;
    let cancelled = false;
    let seenActive = false;

    const tick = async () => {
      const prog = await fetchBollScanStatus();
      if (cancelled || !prog) return;

      const active = prog.running || prog.phase === 'preparing' || prog.phase === 'scanning';
      if (active) {
        seenActive = true;
        return;
      }

      // 未确认过 active 前，忽略残留的 done/failed（避免误判结束）
      if (!seenActive) return;

      if (prog.phase === 'done') {
        setBollScanning(false);
        const matched = prog.matches ?? (prog.summary as { match_rows_upserted?: number } | null)?.match_rows_upserted;
        showToast(
          prog.message ||
            `编排扫描完成${matched != null ? `，命中 ${matched} 条` : ''}`,
        );
        void fetchBollMatches();
        return;
      }

      if (prog.phase === 'failed') {
        setBollScanning(false);
        showToast('编排扫描失败：' + (prog.error || prog.message || '未知错误'));
        return;
      }

      if (prog.phase === 'idle') {
        setBollScanning(false);
      }
    };

    void tick();
    const interval = setInterval(() => { void tick(); }, 1200);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bollScanning, apiBase]);

  // 4.10 渲染高可扩展系统设置 Tab 面板
  const renderSettingsTab = () => {
    return (
      <div className="settings-container">

        {/* 左卡：数据仓库管护与 Terminal 实况监视 */}
        <div className="settings-left-card">
          <div className="settings-section-title">📦 A 股时序行情数据仓库管护</div>

          <div className="stats-dashboard">
            <div className="stats-card">
              <span className="stats-label">基本面个股池总数</span>
              <span className="stats-value">{dbStats.total_stocks} 只</span>
            </div>
            <div className="stats-card">
              <span className="stats-label">实际已落库行情个股数</span>
              <span className="stats-value" style={{ color: 'var(--color-primary)' }}>
                {dbStats.total_bars.toLocaleString()} 只
              </span>
            </div>
            <div className="stats-card">
              <span className="stats-label">行情数据最新交易日</span>
              <span className="stats-value" style={{ color: 'var(--color-gold)' }}>
                {dbStats.latest_bar_date}
              </span>
            </div>
          </div>

          <div className="sync-control-box">
            <button
              className="btn-primary"
              style={{
                padding: '0.65rem 1.5rem',
                fontSize: '0.9rem',
                background: 'linear-gradient(135deg, #10b981 0%, #059669 100%)',
                boxShadow: '0 4px 12px rgba(16, 185, 129, 0.3)',
                border: 'none',
                cursor: 'pointer',
                borderRadius: '6px',
                fontWeight: 'bold'
              }}
              onClick={handleSyncMarketData}
              disabled={syncing}
            >
              <Database size={16} className={syncing ? 'spinner' : ''} style={{ marginRight: '0.4rem', verticalAlign: 'middle' }} />
              {syncing ? '全市场高并发增量同步抓取中...' : '一键同步全市场 A 股行情 (增量续传)'}
            </button>
            <button
              className="btn-primary"
              style={{
                padding: '0.65rem 1.5rem',
                fontSize: '0.9rem',
                background: 'linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)',
                boxShadow: '0 4px 12px rgba(59, 130, 246, 0.3)',
                border: 'none',
                cursor: 'pointer',
                borderRadius: '6px',
                fontWeight: 'bold'
              }}
              onClick={handleSyncTodayData}
              disabled={syncing || syncingToday}
            >
              <Calendar size={16} className={syncingToday ? 'spinner' : ''} style={{ marginRight: '0.4rem', verticalAlign: 'middle' }} />
              {syncingToday ? '当日数据快速同步中...' : '更新获取当日最新数据'}
            </button>
            <p className="sync-safety-tip" style={{ color: 'var(--color-text-muted)', fontSize: '0.72rem', lineHeight: '1.5' }}>
              快速模式仅同步当日缺失数据，已落库当日数据的股票会直接跳过，速度更快、请求更少。
            </p>
            <p className="sync-safety-tip">
              🛡️ <b>自适应断点续传已激活：</b> 重启后端或同步中断后再次启动，系统会自动执行毫秒级秒传，自动跳过已更新完成的历史个股，绝不重头拉取！
            </p>
          </div>

          {/* 极客 Terminal */}
          <div className="terminal-monitor">
            <div className="terminal-header">
              <div className="terminal-dots">
                <span className="dot red"></span>
                <span className="dot yellow"></span>
                <span className="dot green"></span>
              </div>
              <span className="terminal-title">LIVE SYNC FLOW MONITOR (backend_app.log)</span>
            </div>
            <div className="terminal-body">
              {liveLogs.map((log, index) => (
                <div key={index} className="terminal-line">
                  {log}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* 右卡：扩展参数微调面板 */}
        <div className="settings-right-card">
          <div className="settings-section-title">⚙️ 后期扩展量化参数微调配置</div>

          <div className="settings-form">
            <div className="form-item">
              <label>量化计算特征滑动窗口步长 (window_size)</label>
              <input
                type="number"
                value={windowSize}
                onChange={(e) => setWindowSize(Number(e.target.value))}
                min={10}
                max={250}
              />
              <span className="form-item-tip">用于对齐比对和相似度计算中取用个股时序的最大 K 线天数。</span>
            </div>

            <div className="form-item">
              <label>最高并发拉取线程规模 (max_workers)</label>
              <input
                type="number"
                value={maxWorkers}
                onChange={(e) => setMaxWorkers(Number(e.target.value))}
                min={1}
                max={32}
              />
              <span className="form-item-tip">全市场高并发增量拉取时的并行请求线程池规模，8 线程最均衡。</span>
            </div>

            <div className="form-item">
              <label>网络反爬延迟下限 (delay_min, 毫秒)</label>
              <input
                type="number"
                value={delayMin}
                onChange={(e) => setDelayMin(Number(e.target.value))}
                min={0}
                max={5000}
              />
              <span className="form-item-tip">单次请求行情接口后的随机微小延迟下限，防爬封锁守护。</span>
            </div>

            <div className="form-item">
              <label>网络反爬延迟上限 (delay_max, 毫秒)</label>
              <input
                type="number"
                value={delayMax}
                onChange={(e) => setDelayMax(Number(e.target.value))}
                min={0}
                max={5000}
              />
              <span className="form-item-tip">随机微眠延迟上限，过大虽防封，但会导致拉取进度过慢。</span>
            </div>

            <div className="form-item">
              <label>个股行情失败重试上限 (retry_limit)</label>
              <input
                type="number"
                value={retryLimit}
                onChange={(e) => setRetryLimit(Number(e.target.value))}
                min={1}
                max={10}
              />
              <span className="form-item-tip">单只股票网络阻断时的最高重试次数，重试后会自动触发指数级退避。</span>
            </div>

            <div className="form-item">
              <label>标注自更新在线自演化学习率 (η)</label>
              <input
                type="number"
                value={learningRate}
                onChange={(e) => setLearningRate(Number(e.target.value))}
                step={0.01}
                min={0.01}
                max={0.5}
              />
              <span className="form-item-tip">人工 👍极品 / 👎不像 标签对模板特征权重的微调偏置步伐大小。</span>
            </div>

            <button
              className="btn-primary"
              style={{ marginTop: '1.5rem', width: '100%', background: 'linear-gradient(135deg, var(--color-primary) 0%, var(--color-secondary) 100%)' }}
              onClick={handleSaveConfigs}
            >
              保存系统全局参数配置 (一键持久化)
            </button>
          </div>
        </div>

      </div>
    );
  };

  // -----------------------------------------------------------------------------
  // 5. 核心图表渲染配置 (ECharts Custom Configurations)
  // -----------------------------------------------------------------------------

  // 5.0 【大盘看手级专配】 🕯️ 真实行情复权日 K 线烛台 + BOLL 经典三轨主图折线图
  const getBollKlineOption = () => {
    if (!compareData) return {};
    const { cand_bars, candidate_symbol } = compareData;

    const xAxisDates = cand_bars.map(b => b.date);
    const klineData = cand_bars.map(b => [b.open, b.close, b.low, b.high]);
    const bollMid = cand_bars.map(b => b.boll_mid);
    const bollUpper = cand_bars.map(b => b.boll_upper);
    const bollLower = cand_bars.map(b => b.boll_lower);

    const lastBar = cand_bars[cand_bars.length - 1];
    const boSubtitle = lastBar
      ? `MID: ${lastBar.boll_mid?.toFixed(2)}  UB: ${lastBar.boll_upper?.toFixed(2)}  LB: ${lastBar.boll_lower?.toFixed(2)}`
      : '';

    return {
      title: {
        text: `🕯️ 【日K复权主图】 ${candidate_symbol.toUpperCase()} (BOLL 20, 2)`,
        subtext: boSubtitle,
        textStyle: { color: '#f1f5f9', fontSize: 13, fontWeight: 'bold' },
        subtextStyle: { color: '#f59e0b', fontSize: 11, fontFamily: 'monospace' },
        left: 'left',
        top: 0
      },
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', label: { backgroundColor: '#1e293b' } },
        backgroundColor: 'rgba(15, 23, 42, 0.95)',
        borderColor: 'rgba(255,255,255,0.12)',
        textStyle: { color: '#e2e8f0', fontSize: 11 },
        formatter: (params: any) => {
          let html = `<div style="padding: 4px 8px; line-height: 1.6;">`;
          const kParam = params.find((p: any) => p.seriesName === '日K (前复权)');
          if (kParam) {
            const idx = kParam.dataIndex;
            const b = cand_bars[idx];
            html += `<b style="color: #94a3b8;">交易日期: ${b.date}</b><br/>`;
            html += `开盘价: <b style="color:#fff; font-family: monospace;">￥${b.open.toFixed(2)}</b><br/>`;
            html += `收盘价: <b style="color:${b.close >= b.open ? '#ef4444' : '#10b981'}; font-family: monospace;">￥${b.close.toFixed(2)}</b><br/>`;
            html += `最高价: <b style="color:#ef4444; font-family: monospace;">￥${b.high.toFixed(2)}</b><br/>`;
            html += `最低价: <b style="color:#10b981; font-family: monospace;">￥${b.low.toFixed(2)}</b><br/>`;

            const mid = b.boll_mid ? `￥${b.boll_mid.toFixed(2)}` : 'N/A';
            const upp = b.boll_upper ? `￥${b.boll_upper.toFixed(2)}` : 'N/A';
            const low = b.boll_lower ? `￥${b.boll_lower.toFixed(2)}` : 'N/A';

            html += `<div style="margin-top: 6px; padding-top: 6px; border-top: 1px solid rgba(255,255,255,0.1);">`;
            html += `中轨 BOLL-M: <b style="color:#e2e8f0; font-family: monospace;">${mid}</b><br/>`;
            html += `上轨 UB(20): <b style="color:#f59e0b; font-family: monospace;">${upp}</b><br/>`;
            html += `下轨 LB(20): <b style="color:#d946ef; font-family: monospace;">${low}</b><br/>`;
            html += `</div>`;
          }
          html += `</div>`;
          return html;
        }
      },
      legend: {
        data: ['日K (前复权)', 'BOLL-M(20)', 'UB', 'LB'],
        textStyle: { color: '#94a3b8', fontSize: 10 },
        bottom: 5,
        left: 'center'
      },
      grid: { left: '5%', right: '5%', top: '18%', bottom: '15%' },
      xAxis: {
        type: 'category',
        data: xAxisDates,
        axisLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.1)' } },
        axisLabel: { color: '#94a3b8', fontSize: 10 }
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { color: '#94a3b8', fontSize: 10, formatter: '￥{value}' },
        splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.04)', type: 'dashed' } }
      },
      series: [
        {
          name: '日K (前复权)',
          type: 'candlestick',
          data: klineData,
          itemStyle: {
            color: '#ef4444',
            color0: '#10b981',
            borderColor: '#ef4444',
            borderColor0: '#10b981'
          },
          markPoint: {
            data: [
              { type: 'max', valueDim: 'highest', name: '最高', label: { show: true, position: 'top', color: '#fff', fontSize: 9, backgroundColor: 'rgba(239, 68, 68, 0.85)', padding: [3, 5], borderRadius: 3 } },
              { type: 'min', valueDim: 'lowest', name: '最低', label: { show: true, position: 'bottom', color: '#fff', fontSize: 9, backgroundColor: 'rgba(16, 185, 129, 0.85)', padding: [3, 5], borderRadius: 3 } }
            ]
          }
        },
        {
          name: 'BOLL-M(20)',
          type: 'line',
          data: bollMid,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#e2e8f0', width: 1.5, opacity: 0.8 }
        },
        {
          name: 'UB',
          type: 'line',
          data: bollUpper,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#f59e0b', width: 1.5, opacity: 0.8 }
        },
        {
          name: 'LB',
          type: 'line',
          data: bollLower,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#d946ef', width: 1.5, opacity: 0.8 }
        }
      ]
    };
  };

  // 5.0b 布林编排页：单股 K 线 + 布林 + 命中区间 markArea
  const getBollPatternChartOption = () => {
    if (!bollBars.length || !selectedBollMatch) return {};
    const xAxisDates = bollBars.map(b => b.date);
    const klineData = bollBars.map(b => [b.open, b.close, b.low, b.high]);
    const bollMid = bollBars.map(b => b.boll_mid ?? null);
    const bollUpper = bollBars.map(b => b.boll_upper ?? null);
    const bollLower = bollBars.map(b => b.boll_lower ?? null);
    const start = selectedBollMatch.start_date;
    const end = selectedBollMatch.end_date;
    const fmtPx = (v: number | null | undefined, digits = 2) =>
      v == null || Number.isNaN(Number(v)) ? 'N/A' : `￥${Number(v).toFixed(digits)}`;

    return {
      // 标题改到图表外渲染，避免矮图内与坐标系/图例重叠
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        triggerOn: 'mousemove',
        alwaysShowContent: true,
        transitionDuration: 0,
        axisPointer: {
          type: 'line',
          lineStyle: { color: 'rgba(148,163,184,0.45)' },
          label: { show: false },
        },
        backgroundColor: 'rgba(15, 23, 42, 0.95)',
        borderColor: 'rgba(255,255,255,0.12)',
        textStyle: { color: '#e2e8f0', fontSize: 11 },
        formatter: (params: any) => {
          const kParam = (params || []).find((p: any) => p.seriesName === '日K');
          const cached = bollAxisTooltipCacheRef.current;
          if (!kParam) return cached?.html ?? '';
          const idx = kParam.dataIndex as number;
          const cacheKey = String(selectedBollMatch.id);
          if (cached && cached.key === cacheKey && cached.index === idx) {
            return cached.html;
          }
          const b = bollBars[idx];
          if (!b) return cached?.html ?? '';
          let html = `<div style="padding:4px 8px;line-height:1.6;">`;
          html += `<b style="color:#94a3b8;">${b.date}</b><br/>`;
          html += `开 ${fmtPx(b.open)}　收 <b style="color:${b.close >= b.open ? '#ef4444' : '#10b981'};">${fmtPx(b.close)}</b><br/>`;
          html += `低 ${fmtPx(b.low)}　高 ${fmtPx(b.high)}<br/>`;
          html += `<div style="margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.1);">`;
          html += `BOLL-M: <b style="color:#e2e8f0;font-family:monospace;">${fmtPx(b.boll_mid, 3)}</b><br/>`;
          html += `UB: <b style="color:#f59e0b;font-family:monospace;">${fmtPx(b.boll_upper, 3)}</b><br/>`;
          html += `LB: <b style="color:#d946ef;font-family:monospace;">${fmtPx(b.boll_lower, 3)}</b><br/>`;
          const pctB =
            b.pct_b == null || Number.isNaN(Number(b.pct_b))
              ? 'N/A'
              : Number(b.pct_b).toFixed(3);
          html += `%B: <b style="color:#38bdf8;font-family:monospace;">${pctB}</b>`;
          html += `</div></div>`;
          bollAxisTooltipCacheRef.current = { key: cacheKey, index: idx, html };
          return html;
        },
        position: (point: number[], params: any, _dom: HTMLElement, _rect: any, size: { contentSize: number[]; viewSize: number[] }) => {
          const list = Array.isArray(params) ? params : params ? [params] : [];
          const kParam = list.find((p: any) => p?.seriesName === '日K');
          const idx = typeof kParam?.dataIndex === 'number'
            ? kParam.dataIndex
            : bollAxisTooltipCacheRef.current?.index;
          const cacheKey = String(selectedBollMatch.id);
          const posCache = bollAxisTooltipPosRef.current;
          if (posCache && posCache.key === cacheKey && posCache.index === idx && idx != null) {
            return posCache.pos;
          }
          const x = Math.min(point[0], size.viewSize[0] - size.contentSize[0] - 8);
          const y = Math.max(8, point[1] - size.contentSize[1] - 12);
          const pos: [number, number] = [Math.max(8, x), y];
          if (idx != null) {
            bollAxisTooltipPosRef.current = { key: cacheKey, index: idx, pos };
          }
          return pos;
        },
      },
      legend: {
        data: ['日K', 'BOLL-M', 'UB', 'LB'],
        top: 0,
        left: 'center',
        itemGap: 18,
        itemWidth: 16,
        itemHeight: 8,
        icon: 'roundRect',
        textStyle: { color: '#94a3b8', fontSize: 11, padding: [0, 0, 0, 4] },
      },
      grid: { left: 48, right: 16, top: 32, bottom: 28, containLabel: false },
      xAxis: {
        type: 'category',
        data: xAxisDates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.1)' } },
        axisLabel: {
          color: '#94a3b8',
          fontSize: 9,
          hideOverlap: true,
          showMaxLabel: true,
        },
        axisTick: { alignWithLabel: true },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { color: '#94a3b8', fontSize: 9, formatter: '￥{value}' },
        splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.04)', type: 'dashed' } },
      },
      series: [
        {
          name: '日K',
          type: 'candlestick',
          data: klineData,
          itemStyle: {
            color: '#ef4444',
            color0: '#10b981',
            borderColor: '#ef4444',
            borderColor0: '#10b981',
          },
          markArea: {
            silent: true,
            itemStyle: { color: 'rgba(56, 189, 248, 0.08)' },
            data: [[{ xAxis: start }, { xAxis: end }]],
          },
        },
        {
          name: 'BOLL-M',
          type: 'line',
          data: bollMid,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#e2e8f0', width: 1.5, opacity: 0.85 },
        },
        {
          name: 'UB',
          type: 'line',
          data: bollUpper,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#f59e0b', width: 1.5, opacity: 0.85 },
        },
        {
          name: 'LB',
          type: 'line',
          data: bollLower,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#d946ef', width: 1.5, opacity: 0.85 },
        },
      ],
    };
  };

  // 5.0c 个股走势页：单股 K 线 + 布林（无命中 markArea）
  const getStockViewChartOption = () => {
    if (!stockViewBars.length || !selectedStockView) return {};
    const xAxisDates = stockViewBars.map(b => b.date);
    const klineData = stockViewBars.map(b => [b.open, b.close, b.low, b.high]);
    const bollMid = stockViewBars.map(b => b.boll_mid ?? null);
    const bollUpper = stockViewBars.map(b => b.boll_upper ?? null);
    const bollLower = stockViewBars.map(b => b.boll_lower ?? null);
    const fmtPx = (v: number | null | undefined, digits = 2) =>
      v == null || Number.isNaN(Number(v)) ? 'N/A' : `￥${Number(v).toFixed(digits)}`;

    return {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        triggerOn: 'mousemove',
        alwaysShowContent: true,
        transitionDuration: 0,
        axisPointer: {
          type: 'line',
          lineStyle: { color: 'rgba(148,163,184,0.45)' },
          label: { show: false },
        },
        backgroundColor: 'rgba(15, 23, 42, 0.95)',
        borderColor: 'rgba(255,255,255,0.12)',
        textStyle: { color: '#e2e8f0', fontSize: 11 },
        formatter: (params: any) => {
          const kParam = (params || []).find((p: any) => p.seriesName === '日K');
          const cached = stockViewAxisTooltipCacheRef.current;
          if (!kParam) return cached?.html ?? '';
          const idx = kParam.dataIndex as number;
          const cacheKey = selectedStockView.code;
          if (cached && cached.key === cacheKey && cached.index === idx) {
            return cached.html;
          }
          const b = stockViewBars[idx];
          if (!b) return cached?.html ?? '';
          let html = `<div style="padding:4px 8px;line-height:1.6;">`;
          html += `<b style="color:#94a3b8;">${b.date}</b><br/>`;
          html += `开 ${fmtPx(b.open)}　收 <b style="color:${b.close >= b.open ? '#ef4444' : '#10b981'};">${fmtPx(b.close)}</b><br/>`;
          html += `低 ${fmtPx(b.low)}　高 ${fmtPx(b.high)}<br/>`;
          html += `<div style="margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.1);">`;
          html += `BOLL-M: <b style="color:#e2e8f0;font-family:monospace;">${fmtPx(b.boll_mid, 3)}</b><br/>`;
          html += `UB: <b style="color:#f59e0b;font-family:monospace;">${fmtPx(b.boll_upper, 3)}</b><br/>`;
          html += `LB: <b style="color:#d946ef;font-family:monospace;">${fmtPx(b.boll_lower, 3)}</b><br/>`;
          const pctB =
            b.pct_b == null || Number.isNaN(Number(b.pct_b))
              ? 'N/A'
              : Number(b.pct_b).toFixed(3);
          html += `%B: <b style="color:#38bdf8;font-family:monospace;">${pctB}</b>`;
          html += `</div></div>`;
          stockViewAxisTooltipCacheRef.current = { key: cacheKey, index: idx, html };
          return html;
        },
        position: (point: number[], params: any, _dom: HTMLElement, _rect: any, size: { contentSize: number[]; viewSize: number[] }) => {
          const list = Array.isArray(params) ? params : params ? [params] : [];
          const kParam = list.find((p: any) => p?.seriesName === '日K');
          const idx = typeof kParam?.dataIndex === 'number'
            ? kParam.dataIndex
            : stockViewAxisTooltipCacheRef.current?.index;
          const cacheKey = selectedStockView.code;
          const posCache = stockViewAxisTooltipPosRef.current;
          if (posCache && posCache.key === cacheKey && posCache.index === idx && idx != null) {
            return posCache.pos;
          }
          const x = Math.min(point[0], size.viewSize[0] - size.contentSize[0] - 8);
          const y = Math.max(8, point[1] - size.contentSize[1] - 12);
          const pos: [number, number] = [Math.max(8, x), y];
          if (idx != null) {
            stockViewAxisTooltipPosRef.current = { key: cacheKey, index: idx, pos };
          }
          return pos;
        },
      },
      legend: {
        data: ['日K', 'BOLL-M', 'UB', 'LB'],
        top: 0,
        left: 'center',
        itemGap: 18,
        itemWidth: 16,
        itemHeight: 8,
        icon: 'roundRect',
        textStyle: { color: '#94a3b8', fontSize: 11, padding: [0, 0, 0, 4] },
      },
      grid: { left: 48, right: 16, top: 32, bottom: 28, containLabel: false },
      xAxis: {
        type: 'category',
        data: xAxisDates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.1)' } },
        axisLabel: {
          color: '#94a3b8',
          fontSize: 9,
          hideOverlap: true,
          showMaxLabel: true,
        },
        axisTick: { alignWithLabel: true },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { color: '#94a3b8', fontSize: 9, formatter: '￥{value}' },
        splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.04)', type: 'dashed' } },
      },
      series: [
        {
          name: '日K',
          type: 'candlestick',
          data: klineData,
          itemStyle: {
            color: '#ef4444',
            color0: '#10b981',
            borderColor: '#ef4444',
            borderColor0: '#10b981',
          },
        },
        {
          name: 'BOLL-M',
          type: 'line',
          data: bollMid,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#e2e8f0', width: 1.5, opacity: 0.85 },
        },
        {
          name: 'UB',
          type: 'line',
          data: bollUpper,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#f59e0b', width: 1.5, opacity: 0.85 },
        },
        {
          name: 'LB',
          type: 'line',
          data: bollLower,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#d946ef', width: 1.5, opacity: 0.85 },
        },
      ],
    };
  };

  // 5.1 【最强神级 KlineCompareChart 归一化重合 K 线图】
  const getKlineCompareOption = () => {
    if (!compareData) return {};
    const { temp_bars, cand_bars, matched_events } = compareData;

    const tempFirstClose = temp_bars[0]?.close || 1.0;
    const candFirstClose = cand_bars[0]?.close || 1.0;

    const tempClosePercent = temp_bars.map(b => ((b.close - tempFirstClose) / tempFirstClose) * 100);
    const candClosePercent = cand_bars.map(b => ((b.close - candFirstClose) / candFirstClose) * 100);

    const xAxisData = Array.from({ length: temp_bars.length }, (_, i) => `T+${i + 1}`);

    const markPointData = matched_events.map(evt => {
      const idx = cand_bars.findIndex(b => b.date === evt.date);
      if (idx === -1) return null;

      const percentClose = candClosePercent[idx];
      return {
        name: evt.event_type,
        coord: [idx, percentClose + 2.5],
        value: getChineseEventName(evt.event_type),
        label: {
          show: true,
          position: 'top',
          color: '#fff',
          fontSize: 10,
          backgroundColor: 'rgba(16, 20, 35, 0.85)',
          borderColor: evt.confidence >= 0.8 ? '#f59e0b' : '#3b82f6',
          borderWidth: 1,
          borderRadius: 4,
          padding: [4, 6],
          shadowBlur: 8,
          shadowColor: 'rgba(0,0,0,0.5)',
          formatter: (params: any) => {
            return `{title|${params.value}}\n{sub|置信: ${(evt.confidence * 100).toFixed(0)}%}`;
          },
          rich: {
            title: { fontWeight: 'bold', fontSize: 10, color: '#f8fafc' },
            sub: { fontSize: 8, color: '#94a3b8', height: 14 }
          }
        },
        symbol: 'pin',
        symbolSize: 14,
        itemStyle: {
          color: evt.confidence >= 0.8 ? '#f59e0b' : '#3b82f6',
          shadowBlur: 10,
          shadowColor: evt.confidence >= 0.8 ? 'rgba(245, 158, 11, 0.4)' : 'rgba(59, 130, 246, 0.4)'
        },
        evidence: evt.evidence
      };
    }).filter(Boolean);

    return {
      title: {
        text: `【形态重叠对比】 ${compareData.template_symbol.toUpperCase()} (母体) VS ${compareData.candidate_symbol.toUpperCase()} (候选)`,
        textStyle: { color: '#f1f5f9', fontSize: 13, fontWeight: 'bold' },
        left: 'center',
        top: 5
      },
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', label: { backgroundColor: '#1e293b' } },
        backgroundColor: 'rgba(15, 23, 42, 0.95)',
        borderColor: 'rgba(255,255,255,0.12)',
        textStyle: { color: '#e2e8f0', fontSize: 11 },
        formatter: (params: any) => {
          let html = `<div style="padding: 4px 8px; line-height: 1.6;">`;
          const param0 = params[0];
          if (param0) {
            const idx = param0.dataIndex;
            const tDate = temp_bars[idx]?.date || '';
            const cDate = cand_bars[idx]?.date || '';
            html += `<b style="color: #94a3b8;">对齐序列: ${param0.name}</b><br/>`;
            html += `<span style="color: #60a5fa;">● 模板日期: ${tDate}</span><br/>`;
            html += `<span style="color: #f59e0b;">● 候选日期: ${cDate}</span><br/>`;

            params.forEach((p: any) => {
              if (p.seriesName === '模板 (Close%)') {
                html += `模板折算百分比: <b style="color:#60a5fa; font-family: monospace;">${p.value.toFixed(2)}%</b><br/>`;
              } else if (p.seriesName === '候选 (Close%)') {
                html += `候选折算百分比: <b style="color:#f59e0b; font-family: monospace;">${p.value.toFixed(2)}%</b><br/>`;
              }
            });

            const eventToday = markPointData.find((m: any) => m.coord[0] === idx);
            if (eventToday) {
              html += `<div style="margin-top: 6px; padding-top: 6px; border-top: 1px solid rgba(255,255,255,0.1); color: #f59e0b;">`;
              html += `📍 <b>今日捕获事件: ${eventToday.value}</b><br/>`;
              html += `<span style="font-size: 10px; color: #cbd5e1;">证据: ${eventToday.evidence}</span>`;
              html += `</div>`;
            }
          }
          html += `</div>`;
          return html;
        }
      },
      legend: {
        data: ['模板 (Close%)', '候选 (Close%)'],
        textStyle: { color: '#94a3b8', fontSize: 11 },
        bottom: 5,
        left: 'center'
      },
      grid: { left: '5%', right: '5%', top: '15%', bottom: '15%' },
      xAxis: {
        type: 'category',
        data: xAxisData,
        axisLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.1)' } },
        axisLabel: { color: '#94a3b8', fontSize: 10 }
      },
      yAxis: {
        type: 'value',
        name: '起始点归零累计涨跌幅 (%)',
        nameTextStyle: { color: '#94a3b8', fontSize: 9 },
        axisLabel: { color: '#94a3b8', fontSize: 10, formatter: '{value}%' },
        splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.04)', type: 'dashed' } }
      },
      series: [
        {
          name: '模板 (Close%)',
          type: 'line',
          data: tempClosePercent,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#3b82f6', width: 2, opacity: 0.8 },
          itemStyle: { color: '#3b82f6' }
        },
        {
          name: '候选 (Close%)',
          type: 'line',
          data: candClosePercent,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#f59e0b', width: 3, shadowBlur: 10, shadowColor: 'rgba(245, 158, 11, 0.3)' },
          itemStyle: { color: '#f59e0b' },
          markPoint: {
            data: markPointData
          }
        }
      ]
    };
  };

  // 5.2 【雷达图：特征维度权重/分项得分对比】
  const getRadarOption = () => {
    if (activeTab === 'scan' && selectedStock) {
      const breakdown = selectedStock.sub_scores;
      return {
        title: {
          text: `【多维特征对齐雷达】 ${selectedStock.code.toUpperCase()}`,
          textStyle: { color: '#f1f5f9', fontSize: 11, fontWeight: 'bold' },
          left: 'center'
        },
        backgroundColor: 'transparent',
        radar: {
          indicator: [
            { name: '大盘趋势 (Trend)', max: 100 },
            { name: '布林轨道 (Boll)', max: 100 },
            { name: '成交缩量 (Volume)', max: 100 },
            { name: '烛台形态 (Candle)', max: 100 },
            { name: '异常波动 (Volatility)', max: 100 }
          ],
          shape: 'circle',
          axisName: { color: '#94a3b8', fontSize: 9 },
          splitArea: { show: false },
          splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
          axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } }
        },
        series: [{
          name: '分项得分',
          type: 'radar',
          data: [{
            value: [
              breakdown.trend_score,
              breakdown.boll_score,
              breakdown.volume_score,
              breakdown.candle_score,
              breakdown.volatility_score
            ],
            name: '得分',
            areaStyle: { color: 'rgba(139, 92, 246, 0.2)' },
            lineStyle: { color: '#8b5cf6', width: 2 },
            itemStyle: { color: '#8b5cf6' }
          }]
        }]
      };
    }

    if (selectedTemplateDetail) {
      const w = selectedTemplateDetail.weights || {};
      const indicators = Object.keys(w).map(key => ({ name: key, max: 0.5 }));
      const values = Object.values(w);
      return {
        title: {
          text: `【模板特征权重分配雷达】`,
          textStyle: { color: '#f1f5f9', fontSize: 11, fontWeight: 'bold' },
          left: 'center'
        },
        backgroundColor: 'transparent',
        radar: {
          indicator: indicators,
          shape: 'circle',
          axisName: { color: '#94a3b8', fontSize: 9 },
          splitArea: { show: false },
          splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
          axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } }
        },
        series: [{
          name: '维度权重',
          type: 'radar',
          data: [{
            value: values,
            name: '特征权重',
            areaStyle: { color: 'rgba(59, 130, 246, 0.25)' },
            lineStyle: { color: '#3b82f6', width: 2 },
            itemStyle: { color: '#3b82f6' }
          }]
        }]
      };
    }

    return {};
  };

  // 5.3 【回测累计净值渐变面积图 (Equity Curve vs Benchmark)】
  const getBacktestOption = () => {
    if (!backtestResult) return {};
    const { equity_curve } = backtestResult;

    const dates = equity_curve.map(e => e.trade_date);
    const portfolioVals = equity_curve.map(e => e.portfolio_value);
    const benchmarkVals = equity_curve.map(e => e.benchmark_value);

    return {
      title: {
        text: '【回测科学底座】形态持股组合累计净值走势大PK',
        textStyle: { color: '#f1f5f9', fontSize: 13, fontWeight: 'bold' },
        left: 'center',
        top: 5
      },
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(15, 23, 42, 0.95)',
        borderColor: 'rgba(255,255,255,0.12)',
        textStyle: { color: '#e2e8f0', fontSize: 11 }
      },
      legend: {
        data: ['形态持仓组合净值', '业绩对比基准'],
        textStyle: { color: '#94a3b8', fontSize: 11 },
        bottom: 5
      },
      grid: { left: '5%', right: '5%', top: '15%', bottom: '15%' },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.1)' } },
        axisLabel: { color: '#94a3b8', fontSize: 10 }
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (val: number) => val.toFixed(2) },
        splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.04)', type: 'dashed' } }
      },
      series: [
        {
          name: '形态持仓组合净值',
          type: 'line',
          data: portfolioVals,
          smooth: true,
          lineStyle: { color: '#3b82f6', width: 2.5 },
          itemStyle: { color: '#3b82f6' },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(59, 130, 246, 0.3)' },
                { offset: 1, color: 'rgba(59, 130, 246, 0.0)' }
              ]
            }
          }
        },
        {
          name: '业绩对比基准',
          type: 'line',
          data: benchmarkVals,
          smooth: true,
          lineStyle: { color: '#64748b', width: 1.5, type: 'dashed' },
          itemStyle: { color: '#64748b' }
        }
      ]
    };
  };

  // -----------------------------------------------------------------------------
  // 6. UI 主结构渲染 (Main JSX Render)
  // -----------------------------------------------------------------------------
  return (
    <div className="dashboard-container">

      {/* 6.1 Toast 气泡提醒 */}
      {toast && <div className="toast-msg">{toast}</div>}

      {/* 6.2 Header */}
      <header className="dashboard-header">
        <div className="header-left">
          <h1><Sparkles size={24} color="#f59e0b" /> 形态选股归一化研盘工作台</h1>
          <p>朔哥哥好！今日为您提供多维时间扭曲对齐(DTW)量化决策闭环。</p>
        </div>
        <div className="header-right">
          <div className="api-config">
            <Server size={14} color="#3b82f6" />
            <span>后端API:</span>
            <input
              type="text"
              value={apiBase}
              onChange={(e) => setApiBase(e.target.value)}
              placeholder="http://localhost:8000"
            />
          </div>
        </div>
      </header>

      {/* 6.3 Tabs 导航 */}
      <nav className="dashboard-tabs">
        <button
          className={`tab-btn ${activeTab === 'scan' ? 'active' : ''}`}
          onClick={() => setActiveTab('scan')}
        >
          <TrendingUp size={16} /> 极速形态每日扫描大PK
        </button>
        <button
          className={`tab-btn ${activeTab === 'backtest' ? 'active' : ''}`}
          onClick={() => setActiveTab('backtest')}
        >
          <BarChart2 size={16} /> 历史形态滚动仿真回测
        </button>
        <button
          className={`tab-btn ${activeTab === 'boll_pattern' ? 'active' : ''}`}
          onClick={() => setActiveTab('boll_pattern')}
        >
          <Sparkles size={16} /> 布林编排
        </button>
        <button
          className={`tab-btn ${activeTab === 'stock_view' ? 'active' : ''}`}
          onClick={() => setActiveTab('stock_view')}
        >
          <Search size={16} /> 个股走势
        </button>
        <button
          className={`tab-btn ${activeTab === 'templates' ? 'active' : ''}`}
          onClick={() => setActiveTab('templates')}
        >
          <Sliders size={16} /> 形态模板与权重参数自进化
        </button>
        <button
          className={`tab-btn ${activeTab === 'settings' ? 'active' : ''}`}
          onClick={() => setActiveTab('settings')}
        >
          <Settings size={16} /> 系统设置与数据仓库管护
        </button>
      </nav>

      {/* 6.4 内容主体布局 */}
      <main className={(activeTab === 'templates' || activeTab === 'settings' || activeTab === 'boll_pattern' || activeTab === 'stock_view') ? 'full-width-grid' : 'dashboard-grid'}>

        {/* 左侧控制栏 (除模板管理、系统设置、布林编排、个股走势外) */}
        {(activeTab !== 'templates' && activeTab !== 'settings' && activeTab !== 'boll_pattern' && activeTab !== 'stock_view') && (
          <aside className="panel-card">
            <h2 className="panel-title">研盘控制核心</h2>

            <div className="form-group">
              <label>形态模板选择</label>
              <select
                value={selectedTemplateId || ''}
                onChange={(e) => setSelectedTemplateId(Number(e.target.value))}
              >
                {templates.map(tpl => (
                  <option key={tpl.id} value={tpl.id}>{tpl.name}</option>
                ))}
              </select>
            </div>

            {activeTab === 'scan' && (
              <>
                <div className="form-group">
                  <label>扫描交易日期</label>
                  <div style={{ display: 'flex', gap: '0.4rem' }}>
                    <Calendar size={18} color="#94a3b8" style={{ marginTop: '0.5rem' }} />
                    <input
                      type="date"
                      value={runDate}
                      onChange={(e) => setRunDate(e.target.value)}
                    />
                  </div>
                </div>

                <button
                  className="btn-primary"
                  onClick={handleRunMarketScan}
                  disabled={loading}
                >
                  <Play size={16} /> 一键激活全市场扫描
                </button>

                <button
                  className="btn-primary"
                  style={{ background: 'rgba(255,255,255,0.04)', color: '#fff', border: '1px solid var(--border-color)', boxShadow: 'none' }}
                  onClick={fetchScanResults}
                  disabled={loading}
                >
                  <RotateCcw size={16} /> 拉取今日扫描结果
                </button>
              </>
            )}

            {activeTab === 'backtest' && (
              <>
                <div className="form-group">
                  <label>回测开始日期</label>
                  <input
                    type="date"
                    value={btStartDate}
                    onChange={(e) => setBtStartDate(e.target.value)}
                  />
                </div>
                <div className="form-group">
                  <label>回测结束日期</label>
                  <input
                    type="date"
                    value={btEndDate}
                    onChange={(e) => setBtEndDate(e.target.value)}
                  />
                </div>
                <div className="form-group">
                  <label>相似度买入阈值</label>
                  <input
                    type="number"
                    value={btScoreThreshold}
                    onChange={(e) => setBtScoreThreshold(Number(e.target.value))}
                    min={40}
                    max={100}
                  />
                </div>

                <button
                  className="btn-primary"
                  onClick={handleRunBacktest}
                  disabled={loading}
                >
                  <Play size={16} /> 开始形态滚动仿真回测
                </button>
              </>
            )}
          </aside>
        )}

        {/* 右侧展示核心 */}
        <section className="display-area">

          {/* ==================== 1. 每日形态扫描 Tab ==================== */}
          {activeTab === 'scan' && loading && (
            <div className="data-card loading-wrapper">
              <div className="spinner"></div>
              <p>系统哨兵正在全市场极速抓取切片特征大表，扫描 5500 股相似度，请静候 15~40 秒...</p>
            </div>
          )}
          {activeTab === 'scan' && !loading && (
            <>
              {scanResults.length === 0 ? (
                <div className="data-card empty-wrapper">
                  <AlertTriangle size={32} color="#f59e0b" />
                  <p>朔哥哥，当日还没有运行过全市场扫描大PK配置呢。</p>
                  <p style={{ fontSize: '0.8rem' }}>可以配置并点击左侧按钮立即发起，或者拉取最新扫描结果。</p>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', width: '100%' }}>

                  {/* 100% 全宽平铺推荐大表格卡片 */}
                  <div className="data-card" style={{ display: 'flex', flexDirection: 'column', gap: '1rem', width: '100%' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.2rem' }}>
                      <h3 style={{ fontSize: '1.1rem', fontWeight: 'bold', color: '#fff' }}>🎯 全市场形态相似度 Top 推荐大PK</h3>
                      <span style={{ fontSize: '0.8rem', color: 'var(--color-text-muted)' }}>交易日: {runDate}</span>
                    </div>

                    <div className="table-wrapper">
                      <table className="scan-table">
                        <thead>
                          <tr>
                            <th>代码</th>
                            <th>个股名称</th>
                            <th>匹配综合分</th>
                            <th>趋势/布林/成交量/烛台/波动得分</th>
                            <th>反馈标注</th>
                          </tr>
                        </thead>
                        <tbody>
                          {scanResults.map(item => (
                            <tr
                              key={item.id}
                              className={selectedStock?.id === item.id ? 'active-row' : ''}
                              onClick={() => handleSelectStockForCompare(item, true)}
                            >
                              <td style={{ fontWeight: 'bold', fontFamily: 'monospace' }}>{item.code.toUpperCase()}</td>
                              <td>{item.name}</td>
                              <td>
                                <span className={`score-badge ${item.similarity_score >= 0.8 ? 'high-score' : ''}`}>
                                  {(item.similarity_score * 100).toFixed(1)}分
                                </span>
                              </td>
                              <td>
                                <div style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)', display: 'flex', gap: '0.8rem', fontFamily: 'monospace' }}>
                                  <span>势: {item.sub_scores.trend_score.toFixed(0)}分</span>
                                  <span>轨: {item.sub_scores.boll_score.toFixed(0)}分</span>
                                  <span>量: {item.sub_scores.volume_score.toFixed(0)}分</span>
                                  <span>烛: {item.sub_scores.candle_score.toFixed(0)}分</span>
                                  <span>波: {item.sub_scores.volatility_score.toFixed(0)}分</span>
                                </div>
                              </td>
                              <td>
                                <div className="feedback-actions" onClick={e => e.stopPropagation()}>
                                  <button
                                    className={`feedback-btn good ${feedbackVoted[item.id] === 'good_match' ? 'voted' : ''}`}
                                    title="形态极像，一键加星(权重正更新)"
                                    onClick={() => {
                                      setSelectedStock(item);
                                      handleSubmitFeedback('good_match');
                                    }}
                                  >
                                    <Star size={13} fill={feedbackVoted[item.id] === 'good_match' ? 'currentColor' : 'none'} />
                                  </button>
                                  <button
                                    className={`feedback-btn bad ${feedbackVoted[item.id] === 'bad_match' ? 'voted' : ''}`}
                                    title="不像，误判屏蔽(权重负更新)"
                                    onClick={() => {
                                      setSelectedStock(item);
                                      handleSubmitFeedback('bad_match');
                                    }}
                                  >
                                    <Trash2 size={13} fill={feedbackVoted[item.id] === 'bad_match' ? 'currentColor' : 'none'} />
                                  </button>
                                </div>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {/* 侧滑抽屉背景遮罩 (磨砂高透) */}
                  {drawerOpen && (
                    <div
                      className="drawer-mask"
                      onClick={() => setDrawerOpen(false)}
                    ></div>
                  )}

                  {/* 核心右侧侧滑抽屉面板 (Bloomberg Drawer Style) */}
                  <div className={`slide-over-drawer ${drawerOpen ? 'open' : ''}`}>
                    <div className="drawer-header">
                      <div className="drawer-header-left">
                        <Sparkles size={16} color="#f59e0b" style={{ marginRight: '0.4rem', verticalAlign: 'middle' }} />
                        <span style={{ fontSize: '1.1rem', fontWeight: 'bold', color: '#fff' }}>
                          {selectedStock ? `🔍 【形态重叠对比】 ${selectedStock.name} (${selectedStock.code.toUpperCase()})` : '正在拉取比对数据...'}
                        </span>
                      </div>
                      <button
                        className="drawer-close-btn"
                        onClick={() => setDrawerOpen(false)}
                      >
                        ✕
                      </button>
                    </div>

                    <div className="drawer-body">
                      {compareLoading ? (
                        <div className="empty-wrapper" style={{ height: '350px', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                          <div className="spinner"></div>
                          <p style={{ fontSize: '0.85rem', color: 'var(--color-text-muted)' }}>系统哨兵正在微秒对齐 K 线特征通道中，请稍候...</p>
                        </div>
                      ) : compareData && selectedStock ? (
                        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>

                          {/* 图表视角双 Tabs 切换 */}
                          <div style={{ display: 'flex', gap: '0.6rem', marginBottom: '1.2rem', borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '0.8rem' }}>
                            <button
                              className={`tab-btn ${chartView === 'compare' ? 'active' : ''}`}
                              style={{ padding: '0.45rem 1rem', fontSize: '0.8rem', height: 'auto', borderRadius: '6px' }}
                              onClick={() => setChartView('compare')}
                            >
                              🧩 异时空形态百分比归一重合对比
                            </button>
                            <button
                              className={`tab-btn ${chartView === 'boll_kline' ? 'active' : ''}`}
                              style={{ padding: '0.45rem 1rem', fontSize: '0.8rem', height: 'auto', borderRadius: '6px' }}
                              onClick={() => setChartView('boll_kline')}
                            >
                              🕯️ 真实日K线 + BOLL主图指标
                            </button>
                          </div>

                          <div className="compare-container" style={{ margin: 0, border: 'none', background: 'transparent', boxShadow: 'none' }}>

                            {/* 左列：百分比价格走势重叠曲线 */}
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.2rem' }}>
                              <div style={{ height: '420px', width: '100%', background: 'rgba(255,255,255,0.01)', borderRadius: '12px', border: '1px solid var(--border-color)', padding: '1rem' }}>
                                <ReactECharts
                                  option={chartView === 'compare' ? getKlineCompareOption() : getBollKlineOption()}
                                  style={{ height: '100%', width: '100%' }}
                                />
                              </div>

                              {/* 人机交互评语微调 */}
                              <div style={{ background: 'rgba(255,255,255,0.02)', padding: '1rem', borderRadius: '10px', border: '1px solid var(--border-color)' }}>
                                <h4 style={{ fontSize: '0.85rem', fontWeight: 'bold', marginBottom: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                                  <Sparkles size={14} color="#f59e0b" /> 人机反馈微调面板 (Adaptive Learning)
                                </h4>
                                <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
                                  <input
                                    type="text"
                                    style={{ flex: 1, background: '#0a0d16', border: '1px solid var(--border-color)', borderRadius: '6px', color: '#fff', padding: '0.45rem 0.8rem', fontSize: '0.8rem', outline: 'none' }}
                                    placeholder="输入感性研判评语（例如：经典缩量，大赞）..."
                                    value={userComment}
                                    onChange={e => setUserComment(e.target.value)}
                                  />
                                  <button
                                    className="btn-primary"
                                    style={{ padding: '0.45rem 1rem', fontSize: '0.8rem' }}
                                    onClick={() => handleSubmitFeedback('good_match')}
                                  >
                                    极品 (👍)
                                  </button>
                                  <button
                                    className="btn-primary"
                                    style={{ padding: '0.45rem 1rem', fontSize: '0.8rem', background: 'linear-gradient(135deg, #4b5563 0%, #1f2937 100%)', color: '#fff', boxShadow: 'none' }}
                                    onClick={() => handleSubmitFeedback('bad_match')}
                                  >
                                    不像 (👎)
                                  </button>
                                </div>
                              </div>
                            </div>

                            {/* 右列：5维雷达图与AI事实证据 */}
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.2rem', justifyContent: 'space-between' }}>
                              <div style={{ height: '200px', width: '100%', background: 'rgba(255,255,255,0.01)', borderRadius: '12px', border: '1px solid var(--border-color)' }}>
                                <ReactECharts
                                  option={getRadarOption()}
                                  style={{ height: '100%', width: '100%' }}
                                />
                              </div>

                              <div className="explanation-section" style={{ padding: '1.2rem', gap: '0.8rem', flex: 1, minHeight: '260px' }}>
                                <h4 style={{ fontSize: '0.9rem', fontWeight: 'bold', borderBottom: '1px solid var(--border-color)', paddingBottom: '0.4rem', color: '#f1f5f9' }}>
                                  💡 AI 物理事实对齐研判 (Evidences)
                                </h4>
                                <ul className="explanation-list" style={{ gap: '0.6rem' }}>
                                  {compareData.explanation_facts?.positive_facts?.map((fact, idx) => (
                                    <li key={idx} className="explanation-item positive" style={{ fontSize: '0.85rem' }}>
                                      <span className="bullet-dot pos"></span>
                                      <p>{fact.text}</p>
                                    </li>
                                  ))}
                                  {compareData.explanation_facts?.negative_facts?.map((fact, idx) => (
                                    <li key={idx} className="explanation-item negative" style={{ fontSize: '0.85rem' }}>
                                      <span className="bullet-dot neg"></span>
                                      <p>{fact.text}</p>
                                    </li>
                                  ))}
                                </ul>
                                {selectedStock.risk_tips && (
                                  <div style={{ marginTop: '0.5rem', padding: '0.5rem 0.6rem', background: 'rgba(239, 68, 68, 0.04)', borderRadius: '6px', border: '1px solid rgba(239, 68, 68, 0.12)', display: 'flex', gap: '0.4rem', alignItems: 'flex-start' }}>
                                    <AlertTriangle size={12} color="#ef4444" style={{ flexShrink: 0, marginTop: '2px' }} />
                                    <p style={{ fontSize: '0.75rem', color: '#fca5a5', lineHeight: 1.4 }}><b>破位警告：</b>{selectedStock.risk_tips}</p>
                                  </div>
                                )}
                              </div>
                            </div>

                          </div>
                        </div>
                      ) : (
                        <div className="empty-wrapper" style={{ height: '300px', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                          <Info size={32} color="#3b82f6" />
                          <p style={{ fontSize: '0.85rem', color: 'var(--color-text-muted)' }}>暂无股票选中比对数据</p>
                        </div>
                      )}
                    </div>
                  </div>

                </div>
              )}
            </>
          )}

          {/* ==================== 2. 历史形态滚动仿真回测 Tab ==================== */}
          {activeTab === 'backtest' && loading && (
            <div className="data-card loading-wrapper">
              <div className="spinner"></div>
              <p>形态滚动回测引擎正在 A 股历史长河中滚动复盘，无偏回算胜率，请稍候 5~15 秒...</p>
            </div>
          )}
          {activeTab === 'backtest' && !loading && (
            <>
              {backtestResult === null ? (
                <div className="data-card empty-wrapper">
                  <Award size={32} color="#3b82f6" />
                  <p>朔哥哥，当日还未开始滚动形态仿真回测。</p>
                  <p style={{ fontSize: '0.8rem' }}>请在左侧配置历史回测的时间范围及相似度阈值，然后一键发起仿真重演大pk！</p>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>

                  {/* 汇总绩效指标 (Metrics Grid) */}
                  <div className="metrics-grid">
                    <div className="metric-card">
                      <span className="metric-label">触发买入信号数</span>
                      <span className="metric-value" style={{ color: '#fff' }}>{backtestResult.summary.total_signals}</span>
                    </div>
                    <div className="metric-card">
                      <span className="metric-label">5日持有胜率 / 均益</span>
                      <span className="metric-value pos">
                        {backtestResult.summary.winning_rate_5d}% / {backtestResult.summary.avg_return_5d >= 0 ? '+' : ''}{backtestResult.summary.avg_return_5d}%
                      </span>
                    </div>
                    <div className="metric-card">
                      <span className="metric-label">10日持有胜率 / 均益</span>
                      <span className="metric-value pos">
                        {backtestResult.summary.winning_rate_10d}% / {backtestResult.summary.avg_return_10d >= 0 ? '+' : ''}{backtestResult.summary.avg_return_10d}%
                      </span>
                    </div>
                    <div className="metric-card">
                      <span className="metric-label">20日持有胜率 / 均益</span>
                      <span className="metric-value pos">
                        {backtestResult.summary.winning_rate_20d}% / {backtestResult.summary.avg_return_20d >= 0 ? '+' : ''}{backtestResult.summary.avg_return_20d}%
                      </span>
                    </div>
                    <div className="metric-card">
                      <span className="metric-label">最大模拟回撤率</span>
                      <span className="metric-value neg">{backtestResult.summary.max_drawdown}%</span>
                    </div>
                    <div className="metric-card">
                      <span className="metric-label">组合盈亏比</span>
                      <span className="metric-value" style={{ color: '#f59e0b' }}>{backtestResult.summary.profit_loss_ratio}</span>
                    </div>
                  </div>

                  {/* 净值走势图 */}
                  <div className="data-card" style={{ height: '400px' }}>
                    <ReactECharts
                      option={getBacktestOption()}
                      style={{ height: '100%', width: '100%' }}
                    />
                  </div>

                  {/* 成交交易详情列表 */}
                  <div className="data-card">
                    <h4 style={{ fontSize: '1rem', fontWeight: 'bold', marginBottom: '1rem' }}>📋 仿真滚动回测信号落地账单详情 ({backtestResult.trade_details.length}笔)</h4>
                    <div className="table-wrapper">
                      <table className="scan-table" style={{ fontSize: '0.8rem' }}>
                        <thead>
                          <tr>
                            <th>买入日期</th>
                            <th>股票代码</th>
                            <th>个股名称</th>
                            <th>相似评分</th>
                            <th>买入均价</th>
                            <th>5日收益</th>
                            <th>10日收益</th>
                            <th>20日收益</th>
                          </tr>
                        </thead>
                        <tbody>
                          {backtestResult.trade_details.slice(0, 100).map((trade, idx) => (
                            <tr key={idx}>
                              <td>{trade.buy_date}</td>
                              <td style={{ fontWeight: 'bold', fontFamily: 'monospace' }}>{trade.symbol.toUpperCase()}</td>
                              <td>{trade.name || 'A股标的'}</td>
                              <td><span className="score-badge">{trade.score.toFixed(1)}分</span></td>
                              <td style={{ fontFamily: 'monospace' }}>￥{trade.buy_price.toFixed(2)}</td>
                              <td style={{ color: trade.return_5d >= 0 ? 'var(--color-success)' : 'var(--color-danger)', fontFamily: 'monospace', fontWeight: '600' }}>
                                {trade.return_5d >= 0 ? '+' : ''}{(trade.return_5d * 100).toFixed(2)}%
                              </td>
                              <td style={{ color: trade.return_10d >= 0 ? 'var(--color-success)' : 'var(--color-danger)', fontFamily: 'monospace', fontWeight: '600' }}>
                                {trade.return_10d >= 0 ? '+' : ''}{(trade.return_10d * 100).toFixed(2)}%
                              </td>
                              <td style={{ color: trade.return_20d >= 0 ? 'var(--color-success)' : 'var(--color-danger)', fontFamily: 'monospace', fontWeight: '600' }}>
                                {trade.return_20d >= 0 ? '+' : ''}{(trade.return_20d * 100).toFixed(2)}%
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>

                </div>
              )}
            </>
          )}

          {/* ==================== 2b. 布林编排 Tab ==================== */}
          {activeTab === 'boll_pattern' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', width: '100%' }}>
            <div className="boll-pattern-layout" style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 1fr) minmax(420px, 1.4fr)', gap: '1rem', width: '100%' }}>
              <div className="data-card" style={{ padding: '1rem' }}>
                <h2 className="panel-title" style={{ marginBottom: '0.8rem' }}>布林编排命中</h2>
                <div className="form-group">
                  <label>编排筛选</label>
                  <select
                    value={bollPatternFilter}
                    onChange={(e) => setBollPatternFilter(e.target.value)}
                  >
                    <option value="">全部 enabled 编排</option>
                    {bollPatterns.filter(p => p.enabled).map(p => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                </div>
                <div className="form-group">
                  <label>end_within_days（0=全量）</label>
                  <select
                    value={bollEndWithinDays}
                    onChange={(e) => setBollEndWithinDays(Number(e.target.value))}
                  >
                    <option value={3}>近 3 个交易日结束</option>
                    <option value={1}>仅当日结束</option>
                    <option value={0}>全量历史命中</option>
                  </select>
                </div>
                <div className="form-group">
                  <label>扫描窗口 window_days</label>
                  <select
                    value={bollWindowDays}
                    onChange={(e) => setBollWindowDays(Number(e.target.value))}
                  >
                    <option value={60}>60</option>
                    <option value={120}>120</option>
                  </select>
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.8rem' }}>
                  <button className="btn-primary" onClick={handleTriggerBollScan} disabled={bollScanning}>
                    <Play size={14} /> {bollScanning ? '扫描进行中...' : '触发编排扫描'}
                  </button>
                  <button
                    className="btn-primary"
                    style={{ background: 'rgba(255,255,255,0.04)', color: '#fff', border: '1px solid var(--border-color)', boxShadow: 'none' }}
                    onClick={() => { void fetchBollMatches(); }}
                    disabled={bollLoading}
                  >
                    <RotateCcw size={14} /> 刷新列表
                  </button>
                </div>
                {(bollScanning || (bollScanProgress && bollScanProgress.phase !== 'idle')) && bollScanProgress && (
                  <div className="boll-scan-progress" style={{ marginBottom: '0.8rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '0.5rem', marginBottom: '0.35rem' }}>
                      <span style={{ fontSize: '0.78rem', color: 'var(--color-text-muted)' }}>
                        {bollScanProgress.message || '扫描中…'}
                        {bollScanProgress.current_code ? ` · ${bollScanProgress.current_code.toUpperCase()}` : ''}
                      </span>
                      <span style={{ fontSize: '0.78rem', color: 'var(--color-primary)', fontVariantNumeric: 'tabular-nums' }}>
                        {bollScanProgress.total > 0
                          ? `${bollScanProgress.current}/${bollScanProgress.total}（${bollScanProgress.percent}%）`
                          : bollScanProgress.phase === 'preparing'
                            ? '准备中'
                            : bollScanProgress.phase === 'done'
                              ? '完成'
                              : ''}
                      </span>
                    </div>
                    <div className="boll-scan-progress-track">
                      <div
                        className={`boll-scan-progress-fill${bollScanProgress.phase === 'failed' ? ' failed' : ''}${bollScanning && bollScanProgress.total === 0 ? ' indeterminate' : ''}`}
                        style={{
                          width: bollScanProgress.total > 0
                            ? `${Math.min(100, Math.max(bollScanProgress.percent, bollScanning ? 1 : 0))}%`
                            : bollScanProgress.phase === 'done'
                              ? '100%'
                              : bollScanning
                                ? '30%'
                                : '0%',
                        }}
                      />
                    </div>
                    {(bollScanProgress.scanned > 0 || bollScanProgress.matches > 0 || bollScanProgress.errors > 0) && (
                      <p style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', marginTop: '0.35rem' }}>
                        已扫 {bollScanProgress.scanned}
                        {bollScanProgress.skipped > 0 ? ` · 跳过 ${bollScanProgress.skipped}` : ''}
                        {` · 命中 ${bollScanProgress.matches}`}
                        {bollScanProgress.errors > 0 ? ` · 失败 ${bollScanProgress.errors}` : ''}
                      </p>
                    )}
                  </div>
                )}
                <p style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)', marginBottom: '0.6rem' }}>
                  共 {bollMatchTotal} 条 · 按 score 降序
                </p>
                {bollLoading && bollMatches.length === 0 ? (
                  <div className="loading-wrapper"><div className="spinner" /></div>
                ) : bollMatches.length === 0 ? (
                  <div className="empty-wrapper" style={{ padding: '1.5rem 0' }}>
                    <AlertTriangle size={24} color="#f59e0b" />
                    <p style={{ fontSize: '0.85rem' }}>暂无命中。可先触发编排扫描，或将 end_within_days 设为 0。</p>
                  </div>
                ) : (
                  <div className="table-wrapper" style={{ maxHeight: '42vh', overflow: 'auto' }}>
                    <table className="scan-table">
                      <thead>
                        <tr>
                          <th>代码</th>
                          <th>编排</th>
                          <th>区间</th>
                          <th>分</th>
                        </tr>
                      </thead>
                      <tbody>
                        {bollMatches.map(m => (
                          <tr
                            key={m.id}
                            className={selectedBollMatch?.id === m.id ? 'active-row' : ''}
                            onClick={() => { void handleSelectBollMatch(m); }}
                            style={{ cursor: 'pointer' }}
                          >
                            <td>
                              <div style={{ fontWeight: 700, fontFamily: 'monospace' }}>{m.code.toUpperCase()}</div>
                              <div style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)' }}>{m.name || ''}</div>
                            </td>
                            <td style={{ fontSize: '0.75rem' }}>{m.pattern_name}</td>
                            <td style={{ fontSize: '0.72rem', fontFamily: 'monospace' }}>
                              {m.start_date}<br />~ {m.end_date}
                            </td>
                            <td>
                              <span className="score-badge">
                                {m.score != null ? m.score.toFixed(1) : '—'}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div className="data-card" style={{ padding: '1rem', minHeight: '280px' }}>
                {!selectedBollMatch ? (
                  <div className="empty-wrapper">
                    <Info size={28} color="#94a3b8" />
                    <p>选择左侧命中行，查看布林 K 线与高亮区间。</p>
                  </div>
                ) : (
                  <>
                    <div style={{ marginBottom: '0.45rem' }}>
                      <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#f1f5f9', lineHeight: 1.3 }}>
                        布林编排 · {selectedBollMatch.code.toUpperCase()} {selectedBollMatch.name || ''}
                      </div>
                      <div style={{ fontSize: '0.72rem', color: '#94a3b8', marginTop: '0.15rem', lineHeight: 1.35 }}>
                        {selectedBollMatch.pattern_name} | {selectedBollMatch.start_date} ~ {selectedBollMatch.end_date}
                        {' | score '}
                        {selectedBollMatch.score != null ? Number(selectedBollMatch.score).toFixed(2) : '—'}
                      </div>
                    </div>
                    <ReactECharts
                      option={getBollPatternChartOption()}
                      style={{ height: '220px', width: '100%' }}
                      notMerge
                    />
                    <div style={{ marginTop: '0.8rem', fontSize: '0.75rem', color: 'var(--color-text-muted)' }}>
                      <div style={{ marginBottom: '0.45rem' }}>
                        <b style={{ color: '#cbd5e1' }}>命中状态串</b>
                        <div className="boll-zone-string">{selectedBollMatch.matched_states}</div>
                      </div>
                      {bollStateString && (
                        <div>
                          <b style={{ color: '#cbd5e1' }}>近 60 日 zone</b>
                          <div className="boll-zone-string">{bollStateString}</div>
                        </div>
                      )}
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* 编排管理：全局尺子 + CRUD */}
            <div className="data-card" style={{ padding: '1rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.8rem', flexWrap: 'wrap', gap: '0.5rem' }}>
                <h2 className="panel-title" style={{ margin: 0 }}>编排管理（自定义）</h2>
                <button className="btn-primary" onClick={openNewBollForm}>
                  <Sparkles size={14} /> 新建编排
                </button>
              </div>

              <div
                className="boll-manage-grid"
                style={{
                  display: 'grid',
                  gridTemplateColumns: bollShowManage
                    ? 'repeat(2, minmax(300px, 1fr))'
                    : 'minmax(300px, 440px)',
                  gap: '1.25rem',
                  marginBottom: '1rem',
                  alignItems: 'start',
                }}
              >
                <div>
                  <h3 style={{ fontSize: '0.85rem', marginBottom: '0.5rem', color: 'var(--color-text-main)' }}>全局尺子</h3>
                  <div className="form-group">
                    <label>分区（按 L/M/H/U 行编辑交界）</label>
                    <ZoneBoundsEditor
                      bounds={bollGlobalBounds}
                      showInvalid={bollGlobalBoundsInvalid}
                      onChange={next => {
                        setBollGlobalBounds(next);
                        if (bollGlobalBoundsInvalid && isZoneBoundsOrdered(next)) {
                          setBollGlobalBoundsInvalid(false);
                        }
                      }}
                    />
                  </div>
                  <div className="form-group">
                    <label>denoise_min_len（最短持续天数去抖，0=关闭）</label>
                    <input type="number" min={0} value={bollGlobalDenoise} onChange={e => setBollGlobalDenoise(Number(e.target.value))} />
                    <p style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', margin: '0.35rem 0 0', lineHeight: 1.5 }}>
                      匹配正则前，把 zone 游程里短于该天数的段并入相邻段，用来抹掉单日误穿轨等噪声。
                      例：设为 2 会吃掉只持续 1 天的尖刺；过大可能抹掉真实短促突破。不确定时保持 0。
                    </p>
                  </div>
                  <button className="btn-primary" onClick={handleSaveBollSettings}>保存全局尺子</button>
                  {bollSettings && (
                    <p style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', marginTop: '0.4rem' }}>
                      当前 denoise={bollSettings.denoise_min_len}
                    </p>
                  )}
                </div>

                {bollShowManage && (
                  <div style={{ minWidth: 0 }}>
                    <h3 style={{ fontSize: '0.85rem', marginBottom: '0.5rem' }}>
                      {bollEditId ? `编辑编排（id 不可改）` : '新建编排'}
                    </h3>
                    {!bollEditId && (
                      <div className="form-group">
                        <label>id</label>
                        <input value={bollForm.id} onChange={e => setBollForm(f => ({ ...f, id: e.target.value.trim() }))} placeholder="my_pattern_id" />
                      </div>
                    )}
                    <div className="form-group">
                      <label>名称</label>
                      <input value={bollForm.name} onChange={e => setBollForm(f => ({ ...f, name: e.target.value }))} />
                    </div>
                    <div className="form-group">
                      <label>regex（例 L+M{'{3,}'}H*U+H*M{'{3,}'}）</label>
                      <input value={bollForm.regex} onChange={e => setBollForm(f => ({ ...f, regex: e.target.value }))} style={{ fontFamily: 'monospace' }} />
                      {(() => {
                        const d = describeBollRegex(bollForm.regex);
                        return (
                          <p style={{ fontSize: '0.72rem', color: bollRegexDescribeColor(d.kind), margin: '0.4rem 0 0', lineHeight: 1.5 }}>
                            <b style={{ color: 'var(--color-text-main)' }}>走势说明：</b>{d.text}
                          </p>
                        );
                      })()}
                    </div>
                    <div className="form-group">
                      <label>min_total_days</label>
                      <input type="number" value={bollForm.min_total_days} onChange={e => setBollForm(f => ({ ...f, min_total_days: Number(e.target.value) }))} />
                    </div>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.8rem', marginBottom: '0.6rem' }}>
                      <input type="checkbox" checked={bollForm.enabled} onChange={e => setBollForm(f => ({ ...f, enabled: e.target.checked }))} />
                      启用（参与扫描）
                    </label>

                    <div
                      style={{
                        fontSize: '0.72rem',
                        color: 'var(--color-text-muted)',
                        borderTop: '1px solid var(--border-color)',
                        paddingTop: '0.6rem',
                        marginBottom: '0.5rem',
                      }}
                    >
                      尺子覆盖（可选）：不勾选则沿用左侧全局尺子
                    </div>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.8rem', marginBottom: '0.45rem' }}>
                      <input
                        type="checkbox"
                        checked={bollForm.override_zone}
                        onChange={e => {
                          const checked = e.target.checked;
                          setBollForm(f => ({
                            ...f,
                            override_zone: checked,
                            bounds: checked ? f.bounds : { ...bollGlobalBounds },
                          }));
                          if (!checked) setBollFormBoundsInvalid(false);
                        }}
                      />
                      单独定义分区阈值（覆盖全局）
                    </label>
                    {bollForm.override_zone ? (
                      <div className="form-group">
                        <label>本编排分区（覆盖全局）</label>
                        <ZoneBoundsEditor
                          bounds={bollForm.bounds}
                          showInvalid={bollFormBoundsInvalid}
                          onChange={next => {
                            setBollForm(f => ({ ...f, bounds: next }));
                            if (bollFormBoundsInvalid && isZoneBoundsOrdered(next)) {
                              setBollFormBoundsInvalid(false);
                            }
                          }}
                        />
                      </div>
                    ) : (
                      <p style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', margin: '0 0 0.55rem', lineHeight: 1.45 }}>
                        当前沿用全局：
                        L&lt;{formatZoneBound(bollGlobalBounds.m)} ·
                        M[{formatZoneBound(bollGlobalBounds.m)},{formatZoneBound(bollGlobalBounds.h)}) ·
                        H[{formatZoneBound(bollGlobalBounds.h)},{formatZoneBound(bollGlobalBounds.u)}) ·
                        U≥{formatZoneBound(bollGlobalBounds.u)}
                      </p>
                    )}
                    <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.8rem', marginBottom: '0.45rem' }}>
                      <input
                        type="checkbox"
                        checked={bollForm.override_denoise}
                        onChange={e => setBollForm(f => ({
                          ...f,
                          override_denoise: e.target.checked,
                          denoise: e.target.checked ? f.denoise : bollGlobalDenoise,
                        }))}
                      />
                      单独定义 denoise_min_len（最短持续天数去抖）
                    </label>
                    {bollForm.override_denoise ? (
                      <div className="form-group">
                        <label>本编排 denoise_min_len（0=关闭）</label>
                        <input
                          type="number"
                          min={0}
                          value={bollForm.denoise}
                          onChange={e => setBollForm(f => ({ ...f, denoise: Number(e.target.value) }))}
                        />
                        <p style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', margin: '0.35rem 0 0', lineHeight: 1.5 }}>
                          仅本编排生效：短于该天数的 zone 段并入相邻段后再匹配；0=不去抖。
                        </p>
                      </div>
                    ) : (
                      <p style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', margin: '0 0 0.6rem' }}>
                        当前沿用全局 denoise={bollGlobalDenoise}（0=关闭去抖）
                      </p>
                    )}

                    <div style={{ display: 'flex', gap: '0.5rem' }}>
                      <button className="btn-primary" onClick={handleSaveBollPattern}>保存</button>
                      <button
                        className="btn-primary"
                        style={{ background: 'transparent', border: '1px solid var(--border-color)', boxShadow: 'none' }}
                        onClick={() => setBollShowManage(false)}
                      >
                        取消
                      </button>
                    </div>
                  </div>
                )}
              </div>

              <div className="table-wrapper" style={{ maxHeight: '240px', overflow: 'auto' }}>
                <table className="scan-table">
                  <thead>
                    <tr>
                      <th>id</th>
                      <th>名称</th>
                      <th>启用</th>
                      <th>尺子</th>
                      <th>regex / 走势</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bollPatterns.map(p => (
                      <tr key={p.id}>
                        <td style={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>{p.id}</td>
                        <td>{p.name}</td>
                        <td>
                          <input
                            type="checkbox"
                            checked={p.enabled}
                            onChange={e => { void handleToggleBollEnabled(p, e.target.checked); }}
                          />
                        </td>
                        <td style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                          {p.zone_thresholds != null || p.denoise_min_len != null ? '自定义' : '全局'}
                        </td>
                        <td style={{ fontSize: '0.7rem', maxWidth: 260 }}>
                          <div style={{ fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {p.regex}
                          </div>
                          {(() => {
                            const d = describeBollRegex(p.regex || '');
                            return (
                              <div
                                style={{
                                  marginTop: '0.2rem',
                                  color: bollRegexDescribeColor(d.kind),
                                  lineHeight: 1.35,
                                  display: '-webkit-box',
                                  WebkitLineClamp: 2,
                                  WebkitBoxOrient: 'vertical',
                                  overflow: 'hidden',
                                }}
                                title={d.text}
                              >
                                {d.text}
                              </div>
                            );
                          })()}
                        </td>
                        <td>
                          <button
                            className="btn-primary"
                            style={{ padding: '0.2rem 0.5rem', fontSize: '0.72rem', marginRight: '0.3rem' }}
                            onClick={() => openEditBollForm(p)}
                          >
                            编辑
                          </button>
                          {p.enabled && (
                            <button
                              className="btn-primary"
                              style={{ padding: '0.2rem 0.5rem', fontSize: '0.72rem', background: 'rgba(239,68,68,0.2)', boxShadow: 'none' }}
                              onClick={() => { void handleDisableBollPattern(p.id); }}
                            >
                              禁用
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            </div>
          )}

          {/* ==================== 2c. 个股走势 Tab ==================== */}
          {activeTab === 'stock_view' && (
            <div className="data-card stock-view-panel" style={{ padding: '1rem', width: '100%' }}>
              <h2 className="panel-title" style={{ marginBottom: '0.9rem' }}>个股走势</h2>
              <div className="stock-view-toolbar">
                <div className="form-group stock-view-search" ref={stockSearchWrapRef}>
                  <label>搜索股票（名称 / 代码）</label>
                  <div className="stock-search-input-wrap">
                    <Search size={14} className="stock-search-icon" />
                    <input
                      type="text"
                      value={stockSearchQuery}
                      placeholder="如 茅台、600519、sh600519"
                      onChange={(e) => {
                        setStockSearchQuery(e.target.value);
                        setStockSearchOpen(true);
                      }}
                      onFocus={() => {
                        if (stockSearchItems.length > 0) setStockSearchOpen(true);
                      }}
                      autoComplete="off"
                    />
                    {stockSearchLoading && <span className="stock-search-hint">搜索中…</span>}
                  </div>
                  {stockSearchOpen && stockSearchItems.length > 0 && (
                    <ul className="stock-search-dropdown">
                      {stockSearchItems.map((item) => (
                        <li key={item.code}>
                          <button
                            type="button"
                            onClick={() => handleSelectStockView(item)}
                          >
                            <span className="stock-search-code">{item.code.toUpperCase()}</span>
                            <span className="stock-search-name">{item.name}</span>
                            {item.board ? (
                              <span className="stock-search-board">{item.board}</span>
                            ) : null}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  {stockSearchOpen &&
                    !stockSearchLoading &&
                    stockSearchQuery.trim().length > 0 &&
                    stockSearchItems.length === 0 &&
                    !(
                      selectedStockView &&
                      stockSearchQuery.trim() ===
                        `${selectedStockView.code.toUpperCase()} ${selectedStockView.name || ''}`.trim()
                    ) && (
                      <div className="stock-search-empty">无匹配股票</div>
                    )}
                </div>
                <div className="form-group">
                  <label>截止日</label>
                  <div className="date-picker-field">
                    <input
                      ref={stockViewEndDateRef}
                      type="date"
                      className="date-picker-field__input"
                      value={stockViewEndDate}
                      disabled={!selectedStockView}
                      onChange={(e) => setStockViewEndDate(e.target.value)}
                    />
                    <button
                      type="button"
                      className="lookback-stepper__btn date-picker-field__btn"
                      disabled={!selectedStockView}
                      aria-label="打开日期选择"
                      title="选择截止日"
                      onClick={() => {
                        const el = stockViewEndDateRef.current;
                        if (!el || el.disabled) return;
                        try {
                          if (typeof el.showPicker === 'function') {
                            el.showPicker();
                          } else {
                            el.focus();
                            el.click();
                          }
                        } catch {
                          el.focus();
                        }
                      }}
                    >
                      <Calendar size={16} strokeWidth={2.25} />
                    </button>
                  </div>
                </div>
                <div className="form-group">
                  <label>回看日历日</label>
                  <div
                    className="zone-bound-stepper lookback-stepper"
                    style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}
                  >
                    <button
                      type="button"
                      aria-label="减少回看天数"
                      className="zone-bound-stepper__btn lookback-stepper__btn"
                      disabled={!selectedStockView || stockViewLookback <= 30}
                      onClick={() => {
                        setStockViewLookback((v) => Math.min(500, Math.max(30, (v || 120) - 10)));
                      }}
                    >
                      −
                    </button>
                    <input
                      type="number"
                      min={30}
                      max={500}
                      step={10}
                      value={stockViewLookback}
                      disabled={!selectedStockView}
                      className="zone-bound-stepper__input lookback-stepper__input"
                      onChange={(e) => setStockViewLookback(Number(e.target.value) || 0)}
                      onBlur={() => {
                        setStockViewLookback((v) => Math.min(500, Math.max(30, v || 120)));
                      }}
                    />
                    <button
                      type="button"
                      aria-label="增加回看天数"
                      className="zone-bound-stepper__btn lookback-stepper__btn"
                      disabled={!selectedStockView || stockViewLookback >= 500}
                      onClick={() => {
                        setStockViewLookback((v) => Math.min(500, Math.max(30, (v || 120) + 10)));
                      }}
                    >
                      +
                    </button>
                  </div>
                </div>
              </div>

              {!selectedStockView ? (
                <div className="empty-wrapper" style={{ padding: '2.5rem 0' }}>
                  <Info size={28} color="#94a3b8" />
                  <p>输入名称或代码搜索股票，查看日 K + 布林走势。</p>
                </div>
              ) : stockViewLoading && stockViewBars.length === 0 ? (
                <div className="loading-wrapper" style={{ padding: '2rem 0' }}>
                  <div className="spinner" />
                </div>
              ) : stockViewBars.length === 0 ? (
                <div className="empty-wrapper" style={{ padding: '2rem 0' }}>
                  <AlertTriangle size={24} color="#f59e0b" />
                  <p>暂无行情数据，请换截止日或确认该股已同步。</p>
                </div>
              ) : (
                <>
                  <div style={{ marginBottom: '0.45rem' }}>
                    <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#f1f5f9', lineHeight: 1.3 }}>
                      {selectedStockView.code.toUpperCase()} {selectedStockView.name || ''}
                      {stockViewLoading ? (
                        <span style={{ marginLeft: '0.5rem', fontSize: '0.72rem', color: '#94a3b8', fontWeight: 400 }}>
                          刷新中…
                        </span>
                      ) : null}
                    </div>
                    <div style={{ fontSize: '0.72rem', color: '#94a3b8', marginTop: '0.15rem' }}>
                      截止 {stockViewEndDate || '—'} · 回看 {stockViewLookback} 日历日 · BOLL(20,2)
                    </div>
                  </div>
                  <ReactECharts
                    option={getStockViewChartOption()}
                    style={{ height: '400px', width: '100%' }}
                    notMerge
                  />
                  <div style={{ marginTop: '0.75rem' }}>
                    <button
                      type="button"
                      className="stock-zone-toggle"
                      onClick={() => setStockViewZoneOpen((v) => !v)}
                    >
                      {stockViewZoneOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      {stockViewZoneOpen ? '收起 zone' : '显示 zone'}
                    </button>
                    {stockViewZoneOpen && (
                      <div style={{ marginTop: '0.5rem', fontSize: '0.75rem', color: 'var(--color-text-muted)' }}>
                        <b style={{ color: '#cbd5e1' }}>近 60 日 zone</b>
                        {stockViewZoneLoading ? (
                          <div style={{ marginTop: '0.35rem' }}>加载中…</div>
                        ) : (
                          <div className="boll-zone-string">{stockViewZoneString || '—'}</div>
                        )}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          )}

          {/* ==================== 3. 形态模板自学习自更新 Tab ==================== */}
          {activeTab === 'templates' && loading && (
            <div className="data-card loading-wrapper">
              <div className="spinner"></div>
              <p>特征模板管理器正在极速抓取最新自更新的模板权重，请稍候 1~2 秒...</p>
            </div>
          )}
          {activeTab === 'templates' && !loading && renderTemplatesTab()}

          {/* ==================== 4. 系统设置与数据仓库管护 Tab ==================== */}
          {activeTab === 'settings' && !loading && renderSettingsTab()}

        </section>
      </main>
    </div>
  );
}
