-- =============================================================================
-- Pattern Search Engine (PSE) 数据库初始化脚本
-- 支持：TimescaleDB 超表时序数据库 / 原生 PostgreSQL 索引无缝降级
-- 数据库名：stock_datas
-- =============================================================================

create table stocks
(
    code             varchar(12) not null
        primary key,
    name             varchar(64) not null,
    list_date        date,
    board            varchar(32),
    industry         varchar(64),
    is_st            boolean                  default false,
    is_suspended     boolean                  default false,
    updated_at       timestamp with time zone default now(),
    name_pinyin_abbr varchar(64)
);

alter table stocks
    owner to postgres;

create index idx_stocks_board_st
    on stocks (board, is_st)
    where (is_suspended = false);

create index idx_stocks_name_pinyin_abbr
    on stocks (name_pinyin_abbr);

create table daily_bars
(
    code   varchar(12)    not null,
    date   date           not null,
    open   numeric(10, 4) not null,
    high   numeric(10, 4) not null,
    low    numeric(10, 4) not null,
    close  numeric(10, 4) not null,
    volume bigint         not null,
    amount numeric(20, 4) not null,
    factor numeric(16, 6) not null,
    primary key (code, date)
);

alter table daily_bars
    owner to postgres;

create index idx_daily_bars_date_code
    on daily_bars (date desc, code asc);

create table dirty_factors
(
    code         varchar(12) not null,
    dirty_date   date        not null,
    is_processed boolean                  default false,
    updated_at   timestamp with time zone default now(),
    primary key (code, dirty_date)
);

alter table dirty_factors
    owner to postgres;

create table feature_templates
(
    id         serial
        primary key,
    name       varchar(128) not null
        unique,
    type       varchar(32)  not null,
    config     jsonb        not null,
    weights    jsonb        not null,
    created_at timestamp with time zone default now(),
    updated_at timestamp with time zone default now()
);

alter table feature_templates
    owner to postgres;

create table scan_results
(
    id               bigserial
        primary key,
    date             date          not null,
    template_id      integer       not null,
    code             varchar(12)   not null,
    similarity_score numeric(6, 4) not null,
    sub_scores       jsonb         not null,
    explanation      text,
    risk_tips        text,
    created_at       timestamp with time zone default now()
);

alter table scan_results
    owner to postgres;

create index idx_scan_results_date_score
    on scan_results (date asc, template_id asc, similarity_score desc);

create table backtest_reports
(
    id           serial
        primary key,
    template_id  integer not null,
    start_date   date    not null,
    end_date     date    not null,
    metrics      jsonb   not null,
    equity_curve jsonb   not null,
    created_at   timestamp with time zone default now()
);

alter table backtest_reports
    owner to postgres;

create table user_feedback
(
    id         serial
        primary key,
    result_id  bigint      not null,
    label      varchar(32) not null,
    comment    text,
    created_at timestamp with time zone default now()
);

alter table user_feedback
    owner to postgres;

create table technical_indicators
(
    symbol            varchar(16)                            not null,
    trade_date        date                                   not null,
    indicator_version varchar(32)                            not null,
    ma5               numeric(18, 4),
    ma10              numeric(18, 4),
    ma20              numeric(18, 4),
    ma60              numeric(18, 4),
    ma120             numeric(18, 4),
    boll_mid          numeric(18, 4),
    boll_upper        numeric(18, 4),
    boll_lower        numeric(18, 4),
    boll_width        numeric(18, 6),
    boll_width_delta  numeric(18, 6),
    macd              numeric(18, 6),
    rsi14             numeric(18, 6),
    atr14             numeric(18, 6),
    volume_ma20       numeric(20, 2),
    volume_ratio_20   numeric(18, 6),
    created_at        timestamp with time zone default now() not null,
    primary key (symbol, trade_date, indicator_version)
);

alter table technical_indicators
    owner to postgres;

create table feature_vectors
(
    symbol          varchar(16)                                  not null,
    trade_date      date                                         not null,
    feature_version varchar(32)                                  not null,
    features        jsonb                                        not null,
    quality_flags   jsonb                    default '{}'::jsonb not null,
    created_at      timestamp with time zone default now()       not null,
    primary key (symbol, trade_date, feature_version)
);

alter table feature_vectors
    owner to postgres;

create index idx_feature_vectors_features
    on feature_vectors using gin (features);

create table data_sync_status
(
    key        varchar(32) not null
        primary key,
    value      text,
    updated_at timestamp with time zone default now()
);

alter table data_sync_status
    owner to postgres;

create table stock_state_daily
(
    code       varchar(12) not null,
    date       date        not null,
    pct_b      numeric(12, 6),
    zone       varchar(2)  not null,
    updated_at timestamp with time zone default now(),
    primary key (code, date)
);

alter table stock_state_daily
    owner to postgres;

create index idx_stock_state_daily_date
    on stock_state_daily (date desc);

create table pattern_match_result
(
    id             bigserial
        primary key,
    code           varchar(12)                                  not null,
    pattern_id     varchar(64)                                  not null,
    pattern_name   varchar(128)                                 not null,
    start_date     date                                         not null,
    end_date       date                                         not null,
    matched_states text                                         not null,
    score          numeric(10, 4),
    scan_date      date                                         not null,
    window_days    integer                                      not null,
    created_at     timestamp with time zone default now(),
    updated_at     timestamp with time zone default now(),
    edge_hits      jsonb                    default '[]'::jsonb not null,
    indicator_hits jsonb                    default '[]'::jsonb not null,
    unique (code, pattern_id, start_date, end_date)
);

alter table pattern_match_result
    owner to postgres;

create index idx_pattern_match_scan_date
    on pattern_match_result (scan_date desc);

create index idx_pattern_match_end_pattern
    on pattern_match_result (end_date desc, pattern_id asc);

create table boll_pattern_settings
(
    id              integer                  default 1 not null
        primary key
        constraint boll_pattern_settings_id_check
            check (id = 1),
    zone_thresholds jsonb                              not null,
    denoise_min_len integer                  default 0 not null,
    updated_at      timestamp with time zone default now()
);

alter table boll_pattern_settings
    owner to postgres;

create table boll_patterns
(
    id              varchar(64)                                                 not null
        primary key,
    name            varchar(128)                                                not null,
    regex           text                                                        not null,
    min_total_days  integer                  default 0                          not null,
    enabled         boolean                  default true                       not null,
    zone_thresholds jsonb,
    denoise_min_len integer,
    created_at      timestamp with time zone default now(),
    updated_at      timestamp with time zone default now(),
    edges           jsonb                    default '[]'::jsonb                not null,
    period          varchar(16)              default 'daily'::character varying not null,
    indicators      jsonb                    default '[]'::jsonb                not null
);

alter table boll_patterns
    owner to postgres;

create table pattern_match_favorite
(
    id              bigserial
        primary key,
    code            varchar(12)                                                 not null,
    name            varchar(64),
    pattern_id      varchar(64)                                                 not null,
    pattern_name    varchar(128)                                                not null,
    period          varchar(16)              default 'daily'::character varying not null,
    start_date      date                                                        not null,
    end_date        date                                                        not null,
    matched_states  text                                                        not null,
    score           numeric(10, 4),
    edge_hits       jsonb                    default '[]'::jsonb                not null,
    scan_date       date,
    window_days     integer,
    source_match_id bigint,
    note            text                     default ''::text                   not null,
    favorited_at    timestamp with time zone default now(),
    updated_at      timestamp with time zone default now(),
    indicator_hits  jsonb                    default '[]'::jsonb                not null,
    unique (code, pattern_id, start_date, end_date)
);

alter table pattern_match_favorite
    owner to postgres;

create index idx_pattern_match_favorite_at
    on pattern_match_favorite (favorited_at desc);

