param(
    [string]$FromDate = '2024-10-01',
    [string]$TillDate = '2026-10-12',
    [string]$Output = 'data/moex_risk_history_500.json'
)

$ErrorActionPreference = 'Stop'
$baseUrl = 'https://iss.moex.com/iss'
$secids = @('SU26207RMFS9', 'RU000A102HC1', 'RU000A103TX0', 'RU000A102879')

function Convert-BlockRows($Block) {
    $result = [System.Collections.Generic.List[object]]::new()
    foreach ($row in $Block.data) {
        $item = [ordered]@{}
        for ($i = 0; $i -lt $Block.columns.Count; $i++) {
            $item[$Block.columns[$i]] = $row[$i]
        }
        $result.Add([pscustomobject]$item)
    }
    return ,$result
}

$quotes = [System.Collections.Generic.List[object]]::new()
$cashflows = [System.Collections.Generic.List[object]]::new()
$metadata = [System.Collections.Generic.List[object]]::new()
$tradingDates = [System.Collections.Generic.HashSet[string]]::new()

foreach ($secid in $secids) {
    Write-Host "Loading $secid history and terms..."
    $start = 0
    $securityRows = [System.Collections.Generic.List[object]]::new()
    do {
        $url = "$baseUrl/history/engines/stock/markets/bonds/securities/$secid.json?from=$FromDate&till=$TillDate&start=$start&iss.meta=off"
        $payload = Invoke-RestMethod -Uri $url -TimeoutSec 60
        $page = Convert-BlockRows $payload.history
        foreach ($row in $page) { $securityRows.Add($row) }
        $start += $page.Count
    } while ($page.Count -ge 100)

    foreach ($row in $securityRows) {
        $day = [string]$row.TRADEDATE
        [void]$tradingDates.Add($day)
        $quotes.Add([ordered]@{
            secid = [string]$row.SECID
            board_id = [string]$row.BOARDID
            trade_date = $day
            close_price_pct = $row.LEGALCLOSEPRICE ?? $row.CLOSE ?? $row.WAPRICE
            yield_pct = $row.YIELDCLOSE ?? $row.YIELDATWAP
            duration_days = $row.DURATION
            accrued_interest = $row.ACCINT ?? $row.ACCRUEDINT
            z_spread_pct = $row.ZSPREAD
            z_spread_at_wap_pct = $row.ZSPREADATWAPRICE
            volume = $row.VOLUME
            turnover = $row.VALUE
            raw_payload = $row
            source_url = "$baseUrl/history/engines/stock/markets/bonds/securities/$secid.json?from=$FromDate&till=$TillDate"
        })
    }

    $terms = Invoke-RestMethod -Uri "$baseUrl/securities/$secid/bondization.json?iss.meta=off" -TimeoutSec 60
    $couponRows = [System.Collections.Generic.List[object]]::new()
    $couponStart = 0
    do {
        $couponPage = Invoke-RestMethod -Uri "$baseUrl/securities/$secid/bondization.json?coupons.start=$couponStart&iss.meta=off" -TimeoutSec 60
        $couponPageRows = Convert-BlockRows $couponPage.coupons
        foreach ($row in $couponPageRows) { $couponRows.Add($row) }
        $couponStart += $couponPageRows.Count
    } while ($couponPageRows.Count -ge 20)
    $amortRows = Convert-BlockRows $terms.amortizations
    $offerRows = Convert-BlockRows $terms.offers
    $metaQuote = $securityRows | Select-Object -Last 1
    $meta = [ordered]@{
        secid = $secid
        isin = [string]$couponRows[0].isin
        short_name = [string]$metaQuote.SHORTNAME
        name = [string]$couponRows[0].name
        face_value = $metaQuote.FACEVALUE
        face_unit = $metaQuote.FACEUNIT
        bond_type = $metaQuote.BONDTYPE
        coupon_details = $metaQuote.COUPON_DETAILS
        source_url = "$baseUrl/securities/$secid/bondization.json"
    }
    $metadata.Add($meta)
    foreach ($row in $couponRows) {
        if ($row.coupondate -and $row.coupondate -gt '0000-00-00') {
            $cashflows.Add([ordered]@{
                secid = $secid
                cashflow_date = [string]$row.coupondate
                coupon_amount = $row.value_rub
                principal_amount = 0
                face_value = $row.facevalue
                source_payload = $row
            })
        }
    }
    foreach ($row in $amortRows) {
        if ($row.amortdate -and $row.amortdate -gt '0000-00-00') {
            $cashflows.Add([ordered]@{
                secid = $secid
                cashflow_date = [string]$row.amortdate
                coupon_amount = 0
                principal_amount = $row.value_rub
                face_value = $row.facevalue
                source_payload = $row
            })
        }
    }
    foreach ($row in $offerRows) {
        if ($row.offerdate -and $row.offerdate -gt '0000-00-00' -and $row.offertype -notmatch 'Погашение') {
            throw "Instrument $secid has a non-maturity offer at $($row.offerdate); exclude it or model it explicitly."
        }
    }
    Write-Host "  observations=$($securityRows.Count), coupons=$($couponRows.Count), cashflows=$($couponRows.Count + $amortRows.Count)"
}

$days = @($tradingDates | Sort-Object)
Write-Host "Loading end-of-day G-curve for $($days.Count) dates..."
$curves = $days | ForEach-Object -Parallel {
    $day = $_
    $url = "https://iss.moex.com/iss/history/engines/stock/zcyc.json?date=$day&time=18:54:59&iss.meta=off"
    $payload = Invoke-RestMethod -Uri $url -TimeoutSec 60
    $block = $payload.params
    if (-not $block -or $block.data.Count -eq 0) { throw "No G-curve row for $day" }
    $row = $block.data[-1]
    $item = [ordered]@{}
    for ($i = 0; $i -lt $block.columns.Count; $i++) { $item[$block.columns[$i]] = $row[$i] }
    [ordered]@{
        trade_date = [string]$item.tradedate
        trade_time = [string]$item.tradetime
        raw_parameters = $item
        source_url = $url
    }
} -ThrottleLimit 12

$dataset = [ordered]@{
    source = 'MOEX ISS'
    retrieved_at = [DateTime]::UtcNow.ToString('o')
    securities = $secids
    from_date = $FromDate
    till_date = $TillDate
    bond_quotes = @($quotes)
    gcurve_parameters = @($curves)
    instruments = @($metadata)
    cashflows = @($cashflows)
}
$outputPath = Join-Path (Get-Location) $Output
$dataset | ConvertTo-Json -Depth 12 | Set-Content -Path $outputPath -Encoding utf8
Write-Host "Saved $outputPath; quotes=$($quotes.Count), curves=$($curves.Count), cashflows=$($cashflows.Count)"
