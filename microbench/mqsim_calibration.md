# MQSim 校準工作紀錄與執行入口

狀態：**功能短測與24組基準均已完成；趨勢尚未對齊，尚未選出校準參數。**
所有模擬與真機實驗由使用者執行 bash 腳本。準備 XML、讀取既有結果、靜態／離線檢查不會啟動實驗。

## 校準目標：以趨勢變化為主

使用者要求：校準的是負載／QD變化時的反應趨勢，不追求絕對IOPS或latency逐點吻合。
本文件統一保存校準計畫、來源審查、測試紀錄與結果判讀；後續分析程式更新此文件，不另產生每輪Markdown報告。
原始XML、JSON、CSV、log與程式snapshot仍留在各結果目錄，維持可追溯性。

主要比較項目：

1. **吞吐增益**：IOPS(QD) / IOPS(QD1)。檢查增加併發後能帶來多少增益。
2. **延遲成長**：mean_latency(QD) / mean_latency(QD1)。檢查排隊隨需求增加的形狀。
3. **局部增益與斜率**：相鄰測試點的IOPS比值，以及 Δlog(IOPS)/Δlog(QD)。斜率接近1表示在該區間近似線性成長，接近0表示吞吐近似平台；這是描述，非內部飽和證明。
4. **轉折位置與範圍**：看多個連續區間是否穩定趨平，並保留seed／真機repeat的不確定性。不能只用QD1與32判定拐點，也不任意縮放QD軸讓曲線吻合。
5. **跨工作負載的一致性**：同一組模型參數是否能解釋request size、sequential/random與後續batch趨勢。

正規化採每個模擬seed、每個真機repeat自己的QD1，完整保留所有比值，再報中位數與min/max。
另外保留相鄰QD增益，避免QD1快慢變動主導整條曲線。真機repeat不是固定硬體狀態，這些比值仍可能混入時間變動。
不得刪掉快速時段、每QD各調參數、任意拉伸軸、或用補償係數來美化趨勢。

絕對數值只作背景、單位檢查與量級診斷；不以「約25k IOPS」作必須逼近的校準目標。
即使絕對值不同，若趨勢在重複變動範圍內相符，模型可對特定研究問題有用；但可用範圍與剩餘差異必須註明。
同樣地，正規化後曲線吻合也不證明內部硬體結構正確。

## 第一輪的目的

先回答「目前的 MQSim 候選模型，對 4 KiB random read 的 QD 變化如何反應？」
不是直接宣稱重建 GM7。建立實際模擬結果之後，才有依據決定哪些既有機制值得校準、哪些觀測仍無法解釋。
25k IOPS 是真機部分時段的常見平台，不是先驗硬體最大吞吐，也不是必須硬配的常數。

## 來源審查發現

| 項目 | 本地程式依據 | 校準處理 |
|---|---|---|
| Engine 時間 | `src/sim/Sim_Defs.h`，ONE_SECOND=1e9 | 事件時間為 ns，不需導入全域逐 cycle 引擎 |
| XML response time | `src/host/IO_Flow_Base.cpp`，Get_device_response_time / Get_end_to_end_request_delay 除以1000後轉uint32 | XML 已是整數 µs；README 的 ns 說明與實作不同，不再除1000 |
| XML IOPS | 同檔 Report_results_in_XML：generated count / Simulator::Time | 同時核對完成讀取 bytes = planned count×4096，避免未排空仍稱完成IOPS |
| QD workload | `src/host/IO_Flow_Synthetic.cpp`：初始產生QD筆，每次completion補一筆 | 對應continuous refill；不重播有漂移的burst trace |
| I/O大小 | Average_Request_Size 是sector數，SECTOR_SIZE_IN_BYTE=512 | 4KiB=8 sectors；alignment=8，不沿用範例的16-sector alignment |
| Working set | `src/exec/IO_Flow_Parameter_Set.h` 是unsigned int百分比；Host_System.cpp 限制1–100 | 選最接近32GiB的整數百分比，將實際差距明列，不稱完全對齊 |
| 資源與延遲 | `src/ssd/NVM_PHY_ONFI_NVDDR2.cpp` 已有channel busy、command transfer、die execution與事件 | 不再把既有等待重複加到tR上 |
| transaction統計 | `src/ssd/Host_Interface_Base.cpp` / Host_Interface_NVMe.cpp | 匯出execution、transfer、waiting、turnaround µs；不能相加當host-request critical path |

初始 `ssdconfig.xml` 為8 channels、4 chips/channel、2 dies/chip、2 planes/die、8KiB page、MLC、75µs page read等候選值。
這些不是已確認的GM7規格。容量與技術代際也不對齊真機，因此目前診斷結果只能顯示差距，不能直接作正式擬合驗收。

## 實驗設定與固定假設

- 單一flow、4KiB、random uniform、read=100%、QUEUE_DEPTH，QD1/2/4/8/16/32/64/128。
- 三個model seeds：321/322/323，每組100,000筆，完成全部請求才結束，共24次模擬。
- NVMe queue容量沿用設定；它不是生成器QD。腳本要求queue容量大於生成器QD。
- 停用read data cache路徑的工作負載模式：WRITE_CACHE（純讀），但保留現有CMT/mapping模型。
- 在每個獨立case複製XML，明確啟用模擬preconditioning，initial occupancy=75%。這是初始化假設，不代表GM7占用率或實際資料歷史。
- 從本地幾何與overprovisioning，按單flow分配計算可用LBA範圍，再選整數working-set百分比。實際約33.33GiB，與真機32GiB不完全相同；精確數值存在plan.json。
- 使用固定筆數，包含startup/drain，尚無量測窗warmup切分；不能假定100k筆已收斂。真機參考為60秒量測、前置10秒read warmup。
- 隨機數產生器與真機fio不同；相同seed不代表相同地址序列。此輪只對齊分布類型，不宣稱地址逐筆配對。

## 真機參考資料

預設從 `results/mqsim_long_20261112T153235Z_c53192bf/` 的原始fio JSON重新擷取。
先核對全部manifest SHA-256，再輸出每次量測的IOPS、fio latency、來源路徑與來源SHA。
所有快慢時段與重複均保留，不挑選只接近模擬的資料，也不把fio latency直接當NAND tR。

QD1/4/16/64標為fit candidates；2/8/32/128預留比較。因為我們已看過真機資料，這不是盲測或完全獨立驗證。
Burst、request-size與locality曲線是後續交叉檢查，不能以本輪QD曲線吻合就宣稱全面準確。

## 使用者執行指令

先建立少量功能模擬的輸入（只準備檔案，不執行MQSim）：

```bash
cd /home/emmanuel/projects/MQSim
bash microbench/run_mqsim_calibration.sh --prepare --qds 1 32 --seeds 321 --requests 4096
```

把上一步印出的新目錄帶入以下指令，才會編譯並執行MQSim。這不會讀取SIFT檔或啟動fio：

```bash
bash microbench/run_mqsim_calibration.sh --run microbench/results/實際準備目錄
```

功能模擬成功後，準備24個診斷cases並執行：

```bash
bash microbench/run_mqsim_calibration.sh --prepare
bash microbench/run_mqsim_calibration.sh --run microbench/results/上一步印出的目錄
```

每case預設wall-time timeout3600秒；已完成4096筆短測，但100k筆整輪速度尚未量過，不承諾總耗時。失敗保留INCOMPLETE、stdout/stderr，不假造未完成結果。
已執行的目錄拒絕覆寫；修改source後需重新prepare，來源hash不符會拒絕執行。
`--run`會用make強制重新編譯，保存binary SHA、執行參數、各case XML、原始結果及完成manifest。

## 可選：參數敏感度（先看基準，再執行）

```bash
bash microbench/run_mqsim_calibration.sh --prepare --sensitivity
```

這會準備7個候選×8QD×3seeds=168次，仍不執行模擬：

| 變動 | 對應模型行為 | 目前證據狀態 |
|---|---|---|
| reference | 現有設定＋明列初始化 | 未校準候選 |
| tR ±20% | NAND array讀取服務時間 | 敏感度幅度由實驗者指定，非量測範圍 |
| channel rate ±20% | NAND channel資料傳輸時間／可用頻寬 | 同上；不改PCIe頻寬 |
| CMT容量 ×0.5／×2 | mapping table cache容量 | 同上；不能等同真機HMB大小或命中率 |

每個候選固定套用所有QD，不能隨QD換參數。沒有搜尋「最佳配置」或加入QD-specific correction。
必須先看敏感度是否符合機制預期，再找硬體證據限制候選範圍；沒有硬體證據就保留假設，不宣布實際SSD參數。

## 輸出與下一階段

- `plan.json`：source hashes、input hashes、工作範圍、候選變動、初始化假設。
- `real_per_run.csv`：直接從真機原始JSON匯出，保留全部重複。
- 每case原始XML、stdout/stderr、命令；執行binary與hash。
- `analysis/sim_per_run.csv`：模型host/device latency、transaction execution/transfer/waiting。
- `analysis/trend_per_run.csv`：正規化吞吐／延遲、相鄰QD增益與斜率，是主要比較結果。
- `analysis/comparison.csv`：保留絕對數值作追溯；文字判讀及執行紀錄統一寫在本文件。

下一階段需解決working set精確對齊、初始化與warmup邊界、GM7硬體結構證據，再檢查較長請求數的穩定性。
若外部曲線仍無法同時解釋，報告模型缺口，而不是把OS成本灌進tR或亂改channel數。
待模型可用範圍確立，再做NAND transaction一次排入／分批釋出的受控研究。

## 第一輪功能短測人工判讀（合併歷史紀錄）

狀態：兩組執行完整通過；模型尚未校準。
18個result manifest檔案hash核對通過；QD1及QD32各完成4096筆4KiB read，完成讀取bytes與預定數量吻合，無host write。

| QD | MQSim IOPS | 真機IOPS中位數 [min,max] | 模擬host mean µs | 真機fio mean中位數 µs |
|---:|---:|---|---:|---:|
| 1 | 6183.6 | 5647.6 [2568.0,8393.9] | 161 | 176.8 |
| 32 | 111879.4 | 23929.9 [23856.0,24122.9] | 284 | 1337.0 |

QD1落在真機觀察範圍內不能當成已校準；真機本身變動很大。QD32的絕對吞吐比約4.68、延遲比約21.2%，僅保留作歷史背景，不以逼近這些絕對值為校準目標。
趨勢上，模擬QD1→32吞吐增加約18.09倍、延遲增加約1.76倍；真機應用repeat內正規化分布比較，見下方自動紀錄。
這不證明真機只有25k上限，也不能據此直接把tR乘4.68或減少channel數。尚有模型幾何、mapping、狀態、host path與量測邊界等不同。

### 已有延遲機制

| QD | transaction execution µs | transfer µs | waiting µs | turnaround µs |
|---:|---:|---:|---:|---:|
| 1 | 75 | 12 | 72 | 160 |
| 32 | 75 | 12 | 195 | 282 |

各項輸出獨立取整至µs，因此相加可差1µs。這是transaction統計，不是host request各階段互斥分解。

QD1的4096次CMT read查詢中，有3881次miss（約94.75%）、215次hit；模型發出3881次mapping read，共7977次flash read command。
QD32同樣記錄3881次CMT read miss，但mapping read command為3867；miss數與發出command數並不一對一，不能自行補齊或假定相等。
這支持mapping dependency是需要檢查的候選等待來源；目前沒有事件級資料可以把所有waiting逐筆歸因。
兩組Total_GC_Executions均為0，所以不能用這兩組結果推論GC造成了延遲。

### Pipeline利用率欄位的限制

`src/nvm_chip/flash_memory/Flash_Chip.cpp` 的輸出為：

```cpp
Fraction_of_Time_Idle =
    (Simulator->Time() - STAT_totalOverlappedXferExecTime - STAT_totalXferTime)
    / double(Simulator->Time());
```

它沒有直接扣除STAT_totalExecTime。現有execution、transfer、overlap、idle數值不能當作互斥狀態分割後畫成百分比餅圖；QD32相加可超過1。
在追查各counter更新與die交錯語意前，不以這個idle欄位證明pipeline underfill，也不草率修改成1−execution−transfer。
本次沒有修改模擬器行為或歷史輸出。

### 下一步

1. （已完成）執行24組基準，取得完整QD曲線與三個seed；後續判讀見下方基準分析。
2. 觀察mapping miss、transaction waiting與曲線轉折是否一起改變；必要時增加事件級記錄。
3. 在正式擬合前解決32GiB工作範圍精確對齊、初始化與讀取暖機語意。
4. 再決定有證據支持的參數範圍與敏感度實驗。候選不能每個QD各換一組，也不能用任意延遲補償真機慢速狀態。

4096筆僅為功能短測。模擬時間可由N/IOPS估算：QD1約0.662秒、QD32約0.0366秒；它們不是長時間穩態。真機參考含60秒量測窗，仍有時間範圍與邊界差異。

[原始模擬彙整](results/mqsim_calibration_initial_pilot/analysis/sim_per_run.csv) · [絕對數值對照](results/mqsim_calibration_initial_pilot/analysis/comparison.csv)

## 完整基準執行狀態

`results/mqsim_calibration_initial_baseline`：8個QD×3個seed，各100,000筆，共24組。
已確認 COMPLETE，24組皆完成；詳見下方基準結果判讀。

<!-- calibration-run:a35593aaee523ba3:begin -->
## 執行紀錄：mqsim_calibration_initial_pilot

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_initial_pilot`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 32 | 18.093 | 4.224 [2.874, 9.319] | 1.764 | 7.583 [3.435, 11.153] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 6183.6 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 321 | 32 | 111879.4 | 23929.9 [23856.0, 24122.9] | 284.0 | 284.0 | 1337.0 |

### 本輪限制

- Simulator working set approx 33.3312 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.
<!-- calibration-run:a35593aaee523ba3:end -->

<!-- calibration-run:c19c7ccbe071d9f2:begin -->
## 執行紀錄：mqsim_calibration_initial_baseline

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_initial_baseline`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 2 | 1.958 | 0.801 [0.527, 1.776] | 1.025 | 2.497 [1.126, 3.797] |
| reference | 321 | 4 | 3.774 | 3.049 [1.347, 3.831] | 1.062 | 1.312 [1.044, 2.974] |
| reference | 321 | 8 | 7.010 | 2.070 [1.398, 4.699] | 1.143 | 3.867 [1.703, 5.731] |
| reference | 321 | 16 | 12.119 | 7.254 [2.844, 7.798] | 1.323 | 2.206 [2.053, 5.634] |
| reference | 321 | 32 | 18.182 | 4.224 [2.874, 9.319] | 1.764 | 7.583 [3.435, 11.153] |
| reference | 321 | 64 | 22.403 | 6.860 [2.958, 9.665] | 2.863 | 9.339 [6.625, 21.670] |
| reference | 321 | 128 | 24.275 | 4.410 [2.964, 9.697] | 5.286 | 29.057 [13.208, 43.250] |
| reference | 322 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 322 | 2 | 1.958 | 0.801 [0.527, 1.776] | 1.025 | 2.497 [1.126, 3.797] |
| reference | 322 | 4 | 3.772 | 3.049 [1.347, 3.831] | 1.062 | 1.312 [1.044, 2.974] |
| reference | 322 | 8 | 7.013 | 2.070 [1.398, 4.699] | 1.143 | 3.867 [1.703, 5.731] |
| reference | 322 | 16 | 12.110 | 7.254 [2.844, 7.798] | 1.323 | 2.206 [2.053, 5.634] |
| reference | 322 | 32 | 18.215 | 4.224 [2.874, 9.319] | 1.764 | 7.583 [3.435, 11.153] |
| reference | 322 | 64 | 22.408 | 6.860 [2.958, 9.665] | 2.863 | 9.339 [6.625, 21.670] |
| reference | 322 | 128 | 24.299 | 4.410 [2.964, 9.697] | 5.280 | 29.057 [13.208, 43.250] |
| reference | 323 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 323 | 2 | 1.958 | 0.801 [0.527, 1.776] | 1.025 | 2.497 [1.126, 3.797] |
| reference | 323 | 4 | 3.777 | 3.049 [1.347, 3.831] | 1.062 | 1.312 [1.044, 2.974] |
| reference | 323 | 8 | 7.011 | 2.070 [1.398, 4.699] | 1.143 | 3.867 [1.703, 5.731] |
| reference | 323 | 16 | 12.115 | 7.254 [2.844, 7.798] | 1.323 | 2.206 [2.053, 5.634] |
| reference | 323 | 32 | 18.177 | 4.224 [2.874, 9.319] | 1.764 | 7.583 [3.435, 11.153] |
| reference | 323 | 64 | 22.336 | 6.860 [2.958, 9.665] | 2.870 | 9.339 [6.625, 21.670] |
| reference | 323 | 128 | 24.257 | 4.410 [2.964, 9.697] | 5.286 | 29.057 [13.208, 43.250] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 6188.9 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 321 | 2 | 12115.7 | 4524.0 [4426.6, 4560.8] | 165.0 | 165.0 | 441.6 |
| reference | 321 | 4 | 23353.8 | 11303.9 [7828.9, 21633.5] | 171.0 | 171.0 | 353.7 |
| reference | 321 | 8 | 43380.9 | 11734.8 [11691.6, 12065.7] | 184.0 | 184.0 | 681.5 |
| reference | 321 | 16 | 75004.5 | 23872.9 [18629.0, 44038.0] | 213.0 | 213.0 | 670.0 |
| reference | 321 | 32 | 112524.8 | 23929.9 [23856.0, 24122.9] | 284.0 | 284.0 | 1337.0 |
| reference | 321 | 64 | 138650.4 | 24831.5 [24819.4, 38743.4] | 461.0 | 461.0 | 2577.1 |
| reference | 321 | 128 | 150236.9 | 24900.9 [24883.5, 24906.6] | 851.0 | 851.0 | 5140.0 |
| reference | 322 | 1 | 6183.2 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 322 | 2 | 12103.9 | 4524.0 [4426.6, 4560.8] | 165.0 | 165.0 | 441.6 |
| reference | 322 | 4 | 23325.1 | 11303.9 [7828.9, 21633.5] | 171.0 | 171.0 | 353.7 |
| reference | 322 | 8 | 43361.1 | 11734.8 [11691.6, 12065.7] | 184.0 | 184.0 | 681.5 |
| reference | 322 | 16 | 74878.1 | 23872.9 [18629.0, 44038.0] | 213.0 | 213.0 | 670.0 |
| reference | 322 | 32 | 112629.9 | 23929.9 [23856.0, 24122.9] | 284.0 | 284.0 | 1337.0 |
| reference | 322 | 64 | 138555.5 | 24831.5 [24819.4, 38743.4] | 461.0 | 461.0 | 2577.1 |
| reference | 322 | 128 | 150248.5 | 24900.9 [24883.5, 24906.6] | 850.0 | 850.0 | 5140.0 |
| reference | 323 | 1 | 6188.7 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 323 | 2 | 12118.9 | 4524.0 [4426.6, 4560.8] | 165.0 | 165.0 | 441.6 |
| reference | 323 | 4 | 23373.1 | 11303.9 [7828.9, 21633.5] | 171.0 | 171.0 | 353.7 |
| reference | 323 | 8 | 43391.0 | 11734.8 [11691.6, 12065.7] | 184.0 | 184.0 | 681.5 |
| reference | 323 | 16 | 74974.1 | 23872.9 [18629.0, 44038.0] | 213.0 | 213.0 | 670.0 |
| reference | 323 | 32 | 112490.9 | 23929.9 [23856.0, 24122.9] | 284.0 | 284.0 | 1337.0 |
| reference | 323 | 64 | 138229.0 | 24831.5 [24819.4, 38743.4] | 462.0 | 462.0 | 2577.1 |
| reference | 323 | 128 | 150119.9 | 24900.9 [24883.5, 24906.6] | 851.0 | 851.0 | 5140.0 |

### 本輪限制

- Simulator working set approx 33.3312 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.

### 趨勢圖

細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。

![reference：以QD1正規化的吞吐與延遲](results/mqsim_calibration_initial_baseline/analysis/figures/trend_v0_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![reference：以QD32正規化的吞吐與延遲](results/mqsim_calibration_initial_baseline/analysis/figures/trend_v0_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。
<!-- calibration-run:c19c7ccbe071d9f2:end -->


## 完整基準結果判讀：mqsim_calibration_initial_baseline

### 完整性與比較方法

已核對 result_manifest.json 所列150個檔案的SHA-256、輸入雜湊，以及24組XML的生成數與完成讀取bytes。每組100,000筆4KiB讀取，合計2,400,000筆；COMPLETE存在且無INCOMPLETE。此次只分析既有結果，未啟動新實驗、未修改模擬器或調整物理參數。

以下模擬增益先在各seed內計算，再取三個seed中位數；真機先在各repeat內計算，再取三次中位數。不同QD並非同時或固定SSD狀態下量測，repeat配對不代表已控制快慢狀態。範圍為觀測min/max，不是信賴區間。

| 比較區間 | 模擬吞吐倍數 | 真機吞吐倍數中位數 | 模擬平均延遲倍數 | 真機平均延遲倍數中位數 |
|---|---:|---:|---:|---:|
| QD1→32 | 18.182 | 4.224 | 1.764 | 7.583 |
| QD1→128 | 24.275 | 4.410 | 5.286 | 29.057 |
| QD32→128 | 1.335 | 1.041 | 2.996 | 3.845 |

### 最可信的差距：高QD吞吐仍有過多增益

QD32→128時，真機各repeat吞吐只增加3.15–4.40%，延遲增加3.832–3.878倍；模型三個seed吞吐增加33.40–33.51%，延遲增加2.993–2.996倍。這個比較避開波動很大的QD1，也不依賴異常偏快的QD64，較適合作為本輪的趨勢約束。

模擬相鄰QD翻倍的吞吐增益依序約為1.958、1.928、1.858、1.728、1.500、1.230、1.084倍。模型有逐步趨平，但QD32後仍保有比真機這些觀測窗口更大的增益。現有真機QD32與128支持「高QD近似平台」；QD64仍出現一次快速量測，因此不能宣稱整台SSD永遠在同一平台，也不能精確鎖定唯一轉折QD。

這不是單純將全部IOPS乘上一個常數就能消除的差異。固定QD、持續補入的工作負載中，平均延遲與吞吐受在途請求數約束，兩項趨勢並非完全獨立的證據；外部趨平也不證明NAND pipeline已填滿。

### 低QD與隨機種子的限制

真機QD1為2,568–8,394 IOPS；QD4、16、64也有較快量測。真機QD1→2正規化增益中位數甚至只有0.801，不能解讀為SSD增加QD必然降低吞吐。這條曲線混入時間／狀態差異，不應把每個鋸齒都擬合進靜態參數。

各QD的模擬三seed IOPS最大／最小差異僅約0.07–0.31%。這表示本設定對地址seed相對穩定，不表示已重現真機的時變行為；繼續增加地址seed不能替代對真機快慢狀態的解釋。

### 模型內的線索，不是已證實的真機機制

現有transaction平均execution維持75µs、transfer維持12µs；waiting從QD1約72µs升至QD32約194–195µs、QD128約761–762µs。模型確實產生隨負載增加的等待，並非只計算tR加transfer。但這些聚合counter不能定位等待發生於channel、die、mapping依賴或其他資源，也不能相加當成host-request的critical path。先前idle counter語意問題仍未解決，不能以它判定pipeline填滿程度。

100,000筆對應的模擬時窗約為QD1的16.2秒至QD128的0.67秒，仍包含startup/drain。相較4096筆pilot，QD32吞吐只變動約0.6%，可作短測一致性線索，不能據此宣稱所有QD、內部狀態與真機長期平均均已收斂。

### 下一步校準決策

1. 將高QD增益（尤其QD32→128）、局部斜率與轉折區域列為主要約束；低QD正規化保留所有repeat與範圍，不強迫重現鋸齒。不能宣稱已重現長期平均，現有真機僅三次窗口。
2. 先釐清模型mapping等待與channel/die等待的counter及事件邊界，並處理working set精確對齊與量測暖機。現有33.33GiB對32GiB、初始化與量測窗差異仍須明列。
3. 再使用已定義的tR、channel transfer與CMT單因子敏感度，檢查哪些機制改變曲線形狀；±20%或容量倍率只是診斷擾動，不是GM7硬體規格。未知拓撲不能為了配曲線任意減少channel/die數。
4. 候選參數需同時通過其他request size、地址模式與working-set測試的趨勢檢查，再評估burst問題。這輪尚未選參數，也尚不能對內部NAND分批或burst效益作結論。

資料：[趨勢逐次CSV](results/mqsim_calibration_initial_baseline/analysis/trend_per_run.csv)、[模擬逐次CSV](results/mqsim_calibration_initial_baseline/analysis/sim_per_run.csv)、[真機逐次CSV](results/mqsim_calibration_initial_baseline/real_per_run.csv)。

<!-- calibration-run:bb39a1677ec5c61f:begin -->
## 執行紀錄：mqsim_calibration_sensitivity_round1

狀態：已準備 35 個cases，尚未執行。
輸入與來源：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_sensitivity_round1`。
目標：QD吞吐增益、延遲成長及曲線轉折；絕對數值僅供追溯。
<!-- calibration-run:bb39a1677ec5c61f:end -->


## 下一輪：單因子敏感度診斷（已準備，尚未執行）

目標是找出哪些既有硬體機制會改變曲線形狀，不是選出最接近絕對IOPS的設定。本輪不是GM7硬體參數估計，也不是校準完成驗收。前輪三個seed差異很小，因此先用seed321篩選機制，候選再以多seed及其他工作負載驗證。

設定：QD1、8、32、64、128；每組100,000筆4KiB random read；7種設定×5個QD＝35組。保留reference重跑作本輪控制，其他工作負載、初始化、拓撲皆固定。原始輸入存於 `results/mqsim_calibration_sensitivity_round1`。

| 設定 | 變更 | 對應機制／限制 |
|---|---|---|
| reference | 現有參數 | 本輪控制組 |
| tr_minus20 / tr_plus20 | page read latency 75µs→60／90µs | SSD_Device.cpp將頁面類別讀取延遲傳給Flash_Chip；改變NAND讀取服務時間，不加入任意host延遲 |
| channel_slower / channel_faster | Channel_Transfer_Rate 333→250／500 | SSD_Device.cpp:97的整數換算使傳入channel的兩個時序參數由6ns→8／4ns；不是宣稱實際傳輸時間精確按相同比例變動 |
| cmt_half / cmt_double | CMT 2MiB→1／4MiB | Address_Mapping_Unit_Page_Level.cpp:334將bytes除以entry大小換算mapping容量；影響mapping命中與額外讀取，不代表SSD資料快取或HMB容量 |

**修正原先channel ±20%診斷計畫：** 程式採 `floor(1000 / rate) * 2` ns。333與原計畫266都得到6ns，因而原本的減速組可能完全沒有改變channel時序。現在選用相鄰可表示的時序區間，避免把無效擾動誤判為不敏感。只修改實驗輸入生成器，未修改模擬器時序公式；整數量化是模型限制，不是硬體量測證據。

判讀順序：

1. 核對reference與前輪seed321結果是否一致；先確認完成數、manifest與時序有效值。
2. 比較各variant的IOPS(128)/IOPS(32)、latency(128)/latency(32)，以及32→64、64→128局部增益；與同輪reference比較後，再對照真機觀測範圍。
3. 同看QD1→8→32成長，避免只改善高QD卻破壞低QD形狀。稀疏五點只供篩選，不能精確定位轉折。
4. 查原始XML的CMT命中／miss、mapping read與transaction等待；聚合統計只是機制線索，不能用來分配完整host latency。
5. 若參數主要只縮放絕對值、對正規化形狀幾乎無影響，就不能靠它解決趨勢差距；若有方向性影響，也需獨立硬體依據和跨工作負載驗證，不能立即認定GM7使用該參數。

目前working set約33.33GiB對真機32GiB、暖機邊界與真機時變狀態仍未解決；本輪只作模型內控制實驗，不把結果當正式擬合驗收。既有分析會把各variant圖與結果更新到本文件，PNG／SVG保留在該輪analysis/figures。

已離線核對35組輸入、雜湊與變更欄位隔離，沒有啟動模擬。由使用者執行：

```bash
cd /home/emmanuel/projects/MQSim
bash microbench/run_mqsim_calibration.sh --run microbench/results/mqsim_calibration_sensitivity_round1
```


## 校準定位：既有真機證據與程式路徑審查

本節重新核對 `mqsim_suite_sqsmu6q7` 的486個manifest檔案，從原始fio JSON以既有extract函式重算38組非arrival結果（另8組arrival不混入此比較）。沒有執行新實驗。該suite每組30秒量測、10秒ramp、兩個repeat；不是長期平均的充分估計。大小與job數不同時，不宣稱地址序列完全配對。

![既有真機實驗對瓶頸假設的約束](results/mqsim_calibration_initial_baseline/analysis/figures/existing_ssd_evidence.png)

### 證據與可排除的簡單解釋

| 比較 | 重新計算的結果 | 判讀與限制 |
|---|---|---|
| 4KiB/QD32，sequential vs random | 256MiB：seq 223.8–237.6k、random 24.78–24.81k IOPS；32GiB：seq 211.8–223.6k、random 24.01–24.05k | 地址模式有大影響，單一固定host IOPS上限無法解釋所有工作負載。不能因此唯一定位mapping；預取、合併、實體配置與NAND平行度均可能相關 |
| 4KiB random/QD32，256MiB vs32GiB | 兩次repeat小範圍增益約1.033、1.030倍 | 128倍範圍差沒有消除高QD平台，削弱「縮小到256MiB就能解決」的簡單cache假設。不能排除mapping cache：尚不知道容量門檻、歷史與命中率 |
| 總QD32，1job×32 vs8jobs×4 | 24,010.6→24,002.3及23,960.9→23,501.9 IOPS | 這兩次增加job未改善，削弱單一fio工作者供應不足的解釋；未量測實際NVMe queue分配，不能排除所有OS因素 |
| random/QD32，4/64/1024KiB | 約93.8–93.9／124.7–1087.7／1779.6–2399.5MiB/s | 非固定94MiB/s傳輸上限；64KiB差異達8.7倍，不能用兩次不同狀態數據直接擬合channel bandwidth |
| QD1 request size | 4KiB同suite不同組約95–386µs；1024KiB約530–8124µs | 固定延遲＋bytes/bandwidth的回歸會混入狀態差異，現階段不從這些點推導真實tR |

4KiB random/QD32的fio slat約3µs，clat約1.3ms；多數時間在提交後，但clat仍包含OS／裝置路徑，不等於NAND時間。小slat不證明host無瓶頸。較大request可能被OS拆分，fio QD也不等於NAND transaction數。

### 程式審查：現有waiting不是單一資源排隊時間

- `src/ssd/Host_Interface_Base.cpp:107`：read transaction waiting以 `完成時間 - Issue_time - execution - transfer` 計算，是殘餘量，不是獨立量到的channel等待。因此不能把先前QD128約762µs直接稱作NAND contention。
- `src/ssd/Address_Mapping_Unit_Page_Level.cpp:567`、1698附近：存在等待映射的read集合與mapping完成後釋放路徑。這是第一個應補測的時間邊界：資料transaction何時因映射不可用而等待、何時可進入TSU。
- 現行設定為 `PRIORITY_OUT_OF_ORDER`，對應 `src/ssd/TSU_Priority_OutOfOrder.cpp`，不是其他TSU實作。278／292附近檢查channel狀態，340附近在read服務中選擇mapping queue；應追蹤mapping/user兩類工作如何共享服務機會。
- `src/ssd/Flash_Transaction_Queue.cpp`已在enqueue/dequeue做probe；`src/ssd/Queue_Probe.cpp:172`輸出的等待已轉為整數µs。可以先利用現有queue輸出，但它只涵蓋該queue停留時間，不包含全部映射等待或下游傳輸等待。

### 下一步已鎖定的工作，而非無限制掃描

**優先修改觀測位置：Address_Mapping_Unit_Page_Level → 現行TSU → NVM_PHY_ONFI_NVDDR2。** 先補上transaction建立、mapping阻塞起訖、TSU入列／派送、command／data transfer及die execution的事件關聯；以同一request ID串起多個transaction。要驗證每個時間戳的語意、重入與重疊，不以各transaction平均值相加冒充request critical path。核心排程行為暫不修改。

最有判別力的下一個模型實驗是**地址模式×working set**，固定4KiB、QD32，並取得mapping命中、mapping讀取數、mapping阻塞與TSU等待：

- 若模型縮小working set後大量改善，而真機只約3%，先查模型mapping容量／工作範圍／初始化與mapping讀取機制；不能只把CMT調到曲線吻合。
- 若mapping相關等待變化很小，但模型無法重現sequential/random的大差距，將優先檢查地址配置、請求合併、預取及資源排程，仍需額外證據區分它們。
- 若模型能重現地址模式與working-set趨勢，卻仍在高QD有過大增益，再針對有效平行度與資源服務重疊定位。

這組實驗尚不能直接交付執行：現有生成器固定RANDOM_UNIFORM及整數working-set百分比，256MiB在現有容量下不足1%，不能誠實聲稱已與真機對齊。需先審查／實作精確地址範圍、相應地址模式與觀測，並驗證初始化和量測邊界。先前35組敏感度保留為輔助，不作此步驟的先決條件。此輪沒有為吻合數據而修改tR、拓撲或加入人造延遲。


## 原始碼修改：精確工作範圍（已編譯，尚未執行模擬驗證）

已修改 `src/exec/IO_Flow_Parameter_Set.{h,cpp}`、`src/exec/Host_System.cpp`、`src/host/IO_Flow_Synthetic.{h,cpp}`。新增可選XML欄位 `Working_Set_Sectors`，單位為512-byte sector；0或未指定時維持既有百分比地址生成路徑。指定非零時覆蓋百分比所決定的工作範圍，範圍不得超出flow配置容量。

- 256MiB：`<Working_Set_Sectors>524288</Working_Set_Sectors>`。
- 32GiB：`<Working_Set_Sectors>67108864</Working_Set_Sectors>`。
- 精確模式目前只接受FIXED request size、alignment等於request size、起點與範圍均對齊，地址模式為RANDOM_UNIFORM或STREAMING。其他組合拒絕，避免默默產生不同分布。
- RANDOM_UNIFORM以完整request block為均勻取樣單位，可取到最後完整區塊；STREAMING從範圍起點開始、最後一筆後回捲。這是明確定義的主機工作負載語意，不是新增SSD硬體機制。
- 工作範圍比例也更新供既有初始化統計使用；preconditioning與暖機的等價性尚未驗證，不能宣稱已與fio完全對齊。

驗證：`make -B -j2`完整重編譯成功；尚未執行MQSim或fio，不能把編譯通過當成執行正確。下一步還需要可由使用者執行的邊界短測、legacy模式回歸，以及mapping／TSU／PHY事件觀測。此刻尚未完成整套新實驗，不應開始正式參數擬合。

**舊準備目錄注意：** 原始碼雜湊已改變，因此之前準備但未執行的 `mqsim_calibration_sensitivity_round1` 不再適用目前來源；原執行器會拒絕。不可修改舊manifest繞過檢查，後續須建立新輸入目錄。已完成的歷史輸出與binary snapshot保持原狀。


## Mapping／TSU觀測實作與下一個驗證入口

已修改MQSim原始碼，增加不參與排程決策的觀測欄位：

- `NVM_Transaction.h`：transaction各自保存mapping集合進入時間、累計停留ns、episodes，以及TSU累計停留ns／departures。
- `Address_Mapping_Unit_Page_Level.cpp`：進入 `Waiting_unmapped_read_transactions` 時開始，mapping完成回呼取出該entry並轉交後續流程前結束。只測這個集合中的停留；不包含全部FTL查詢成本、其他GC barrier或cache等待。
- `Queue_Probe.cpp`：利用既有enqueue/dequeue時間戳，將queue停留累加到transaction。現有呼叫點來自Flash_Transaction_Queue。若未來增加其他使用者需重新審查命名；queue停留也不能區分channel忙與chip忙。
- `Host_Interface_Base`與NVMe XML：在現有cache→host transaction完成通知累計，輸出Observed_Read_Completed、Mapping_Blocked_Count、Mapping_Episodes、TSU_Departures、Open_Mapping_At_Completion，以及Mapping_Wait_Sum_NS／TSU_Queue_Sum_NS。

計數母體是既有完成通知所見read transactions，不是所有mapping／GC transactions，也不是host request數的通用替代。以sum_ns／completed算包含零等待的平均；mapping條件平均可另以blocked_count作分母。這兩個觀測是既有residual waiting的部分來源，不可再加在原waiting上。尚未實作PHY逐階段時間戳、request ID關聯或完整critical path；不能宣稱已取得完整SSD延遲分解。

驗證狀態：完整 `make -B -j2` 通過，bash語法、Python語法與diff空白檢查通過；沒有執行MQSim或fio。功能回歸必須由使用者執行以下腳本：

```bash
cd /home/emmanuel/projects/MQSim
bash microbench/run_observation_check.sh
```

六組：兩組legacy（QD1、32）應與已封存pilot的IOPS及既有latency指標完全一致；另四組是256MiB／32GiB × random／streaming，QD32，各4096筆。沿用pilot初始化與裝置設定，固定seed321。驗證完成bytes、read觀測數、mapping已關閉、TSU departure數；差異即停止並保留INCOMPLETE。每次建立新目錄，保存binary、來源雜湊、輸入、stdout/stderr、XML及結果manifest，紀錄回寫本文件。

這六組只檢查功能與觀測不改變legacy數值，不能證明均勻分布、範圍邊界、暖機或長期收斂；特別是4096筆streaming不會覆蓋256MiB。通過後仍需LBA邊界驗證與正式量測窗設計，才用來判定mapping／排程的校準方向。依先前pilot每組約3分鐘，粗估六組約20分鐘，但精確範圍可能改變初始化成本，尚無本次實測時間。

<!-- calibration-run:7e8078b0b9e3ba6b:begin -->
## 觀測驗證：mqsim_observation_check_df32f044

狀態：INCOMPLETE，Unexpected transaction population; inspect raw output；保留原始輸出於 `/home/emmanuel/projects/MQSim/microbench/results/mqsim_observation_check_df32f044`。
<!-- calibration-run:7e8078b0b9e3ba6b:end -->


## 觀測短測失敗定位與修正：mqsim_observation_check_df32f044

本輪不是六組完成，而是在第二組legacy_qd32完成模擬後被檢查器停止。14個封存檔案雜湊通過。離線重新比對兩組XML：QD1、QD32的IOPS、device/end-to-end latency與原有transaction turnaround/execution/transfer/waiting皆與歷史pilot完全一致。失敗在新觀測分類，不是既有性能指標改變。

QD1完成4096筆、mapping blocked 3881筆、queue departures 4096；QD32完成4096筆、mapping blocked 3881筆，但queue departures達5206，因此原本「TSU departures=完成數」的檢查停止。四組精確範圍案例尚未執行。

根因：前次對Queue_Probe使用者的審查不完整。`NVM_PHY_ONFI_NVDDR2.cpp`也建立Flash_Transaction_Queue：WaitingReadTX、WaitingGCRead_TX、WaitingMappingRead_TX。約509–524行將讀取執行完成但無法立即傳出的transaction入列，約466–475行在channel可服務時取出。舊觀測把TSU排隊和PHY資料傳出排隊混加，不能把舊TSU_Queue_Sum_NS全解釋為排程器等待。5206−4096=1110是額外queue departure的差額，單憑舊彙總不能追溯每筆歸屬，不能事後合成拆分數據。

已修正：PHY三個queue明確標記為data-out queue，Queue_Probe依分類分別累積；NVMe XML新增Observed_Read_PHY_Data_Out_Queue_Sum_NS及Observed_Read_PHY_Data_Out_Departures。原TSU檢查維持，並要求本純讀短測PHY出列數介於0與完成數之間。PHY欄位只量等待佇列停留，不包含實際data transfer，也不代表所有PHY等待。

保留舊INCOMPLETE與所有原始資料。再次執行 `bash microbench/run_observation_check.sh` 會建立新目錄，完整重跑六組，不覆蓋舊結果。新增分類的執行正確性仍待使用者重跑確認；未調整任何NAND時間、排程優先權或服務事件。

<!-- calibration-run:cb836ed0eb3f71b5:begin -->
## 觀測驗證：mqsim_observation_check_322228e8

狀態：六組完成，legacy數值回歸與計數檢查通過；不是正式校準。

原始輸出：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_observation_check_322228e8`。

| Case | Completed reads | Mapping mean ns / all reads | TSU mean ns / all reads | PHY data-out queue mean ns / all reads |
|---|---:|---:|---:|---:|
| legacy_qd1 | 4096 | 72812.335 | 0.000 | 0.000 |
| legacy_qd32 | 4096 | 108163.238 | 84617.663 | 2227.959 |
| RANDOM_UNIFORM_256MiB | 4096 | 24381.834 | 74285.145 | 3966.456 |
| STREAMING_256MiB | 4096 | 98292.288 | 55307.285 | 4474.595 |
| RANDOM_UNIFORM_32768MiB | 4096 | 107556.703 | 85456.946 | 2226.089 |
| STREAMING_32768MiB | 4096 | 98292.288 | 55307.285 | 4474.595 |

僅4096筆功能驗證：不保證工作範圍已走完／cache已暖機，不以此表推論容量或正式趨勢；尚無逐筆LBA邊界稽核。
<!-- calibration-run:cb836ed0eb3f71b5:end -->


## 六組觀測驗證判讀：322228e8（2026-11-14）

重新驗證result_manifest列出的35個檔案，全數通過；COMPLETE存在、無INCOMPLETE。從六組原始XML重新核對：每組4096筆讀取完成、TSU出列4096次、完成時mapping active為0。legacy QD1與QD32所有既有解析性能指標與歷史pilot完全一致。這證明本輪回歸案例未見觀測改變輸出，不代表所有工作負載都已驗證。

![Mapping、TSU與PHY等待觀測](results/mqsim_observation_check_322228e8/analysis/wait_observations.png)

| Case | IOPS | Host end-to-end mean µs | Mapping blocked / 4096 | PHY data-out queue departures |
|---|---:|---:|---:|---:|
| legacy QD1 | 6183.6 | 161 | 3881 | 0 |
| legacy QD32 | 111879.4 | 284 | 3881 | 1110 |
| random 256MiB | 164841.9 | 192 | 991 | 1354 |
| sequential 256MiB | 126750.3 | 251 | 4005 | 1513 |
| random 32GiB | 111813.7 | 284 | 3857 | 1124 |
| sequential 32GiB | 126750.3 | 251 | 4005 | 1513 |

### 已確認的觀測與尚不能下的結論

1. legacy QD32現在分成TSU出列4096次、PHY等待出列1110次，對應先前誤合併的5206次。平均mapping等待108.16µs、TSU排隊84.62µs、PHY傳出排隊2.23µs。這些是同一完成read transaction母體的階段觀測；不能加到原residual waiting上，也不能當所有host request的critical path。
2. 模型random 256MiB／32GiB吞吐比約1.474，而真機既有兩次約1.030–1.033。模型mapping blocked比例由94.17%降至24.19%，指向mapping／初始化值得優先查。但blocked比例是集合停留事件，不等於CMT miss rate；短測與真機30秒＋10秒ramp的狀態不同，尚不能據此調CMT容量。
3. 模型32GiB sequential／random比約1.134，真機同範圍約8.82–9.29。此差距目前混有短測、初始化與地址歷史差異，不能立即加入預取或硬改scheduler來追上真機。
4. 兩個sequential case均只送4096×4KiB＝16MiB，未達256MiB的第一圈，更未涵蓋32GiB；相同結果符合生成設定，不是證明working-set無影響。sequential有4005筆進入mapping等待，可能包含共等尚未完成的mapping讀取；不得解讀為4005次獨立mapping NAND read。

### 下一個具體校準步驟

本次先驗證了觀測工具，下一步應以Address_Mapping_Unit_Page_Level及初始化為優先查核對象：匯出CMT查詢／miss、真正mapping read命令、共享等待筆數，對照相同地址範圍下各量測窗口的變化。先完成地址邊界／回捲檢查及暖機與量測窗分離，才能區分「短測初始化」與「mapping模型本身」；不需要現在再重跑相同六組。先前的35組參數掃描仍非先決條件，尚無已校準的硬體參數。

本次僅分析既有輸出並產圖；沒有啟動新模擬、沒有改變硬體或排程參數。


## 分段量測腳本與既有log擴充（2026-11-14）

先審查現有IO_Flow_Base response-time log：已有模擬時間與短期平均device/end-to-end latency，因此不新增重複事件log。沿用 `workload.IO_Flow.No_0.log`，保留原前三欄，新增精確time_ns、累計completed/generated、latency/device總和ns、read bytes、全裝置read CMT queries/misses與mapping讀取計數。mapping讀取計數來自現有Stats::Total_flash_reads_for_mapping，是FTL建立／提交路徑的計數，不是精確NAND發出或完成時間。

checkpoint依既有completion回呼觸發：越過logging period後寫入，另補初始與固定請求數最後完成的snapshot；不新增模擬排程事件。修正short-term delay分母的空窗口檢查。log未啟用時不輸出，checkpoint不是逐筆request/NAND事件軌跡；目前不宣稱已量到每筆command的issue/completion。NVMe的mapping／TSU／PHY觀測仍在最後XML，尚未進入分段log。

新增 `mqsim_window_probe.py` 與 `run_window_probe.sh`。四組：QD32、4KiB、seed321，256MiB／32GiB × RANDOM_UNIFORM／STREAMING，使用精確sector範圍；初始化沿用pilot。每10ms模擬時間附近的完成事件記錄一次checkpoint。後處理以累計差值重算窗口IOPS、完成請求平均latency、CMT queries/misses與mapping讀取數，產生windows.csv與PNG/SVG並更新本文件。

預先選定warmup切點1模擬秒，不重置快取／模型狀態。跨切點窗口與generated達上限的最後區間不納入後段摘要；後段仍須檢查趨勢是否漂移，1秒不等於已證實穩態。窗口latency採「在此窗口完成」母體，可能包含窗口前已送入的請求；mapping計數與host completion不是一一對應。單flow下才解讀全裝置mapping/CMT計數。

先由使用者執行功能短測（每組65,536筆＝256MiB）：

```bash
cd /home/emmanuel/projects/MQSim
bash microbench/run_window_probe.sh --smoke
```

短測可能不到1模擬秒；後段摘要顯示N/A屬合理結果，不以此測穩態。四組用於確認log欄位、完整snapshot與分析流程。正式模式每組16,777,216筆＝64GiB，足以讓STREAMING走過32GiB兩圈，但不保證暖機充分；執行時間尚無實測，先檢查短測再決定是否跑正式模式。每case timeout4小時。腳本不執行fio，不替使用者啟動實驗。

已完成C++完整編譯、Python與bash語法及diff檢查；新腳本的真實模擬執行仍待使用者驗證。每次建立新目錄，保存binary、來源hash、腳本、輸入、log、XML、結果manifest。原始輸出完整時標記COMPLETE；分析另需成功才寫入結果表，不把COMPLETE單獨當成校準完成。

<!-- calibration-run:7e196bb5e4f6096d:begin -->
## 分段checkpoint實驗：mqsim_windows_5cd221f4

狀態：執行完成；warmup切點 1 模擬秒為預先選定診斷設定，不保證已穩態。

| Case | 後段完成數 | 後段IOPS | 後段mean latency µs |
|---|---:|---:|---:|
| RANDOM_UNIFORM_256MiB | 0 | N/A | N/A |
| RANDOM_UNIFORM_32768MiB | 0 | N/A | N/A |
| STREAMING_256MiB | 0 | N/A | N/A |
| STREAMING_32768MiB | 0 | N/A | N/A |

![分段趨勢](results/mqsim_windows_5cd221f4/analysis/windows.png)

原始log時間為模擬時間。每段在完成事件越過period後記錄，非精確等寬窗口；最後段較短，mapping讀取數不可直接視作固定時間速率。latency以該窗口完成的requests為母體，包含跨窗口到達的工作。CMT／mapping為全裝置累計差值，本實驗僅單flow，不與完成數硬配對。startup、跨warmup邊界及最後generated達上限的區間不納入後段彙總；仍應檢查後段是否持續漂移。功能短測不代表穩態。
<!-- calibration-run:7e196bb5e4f6096d:end -->


## Checkpoint短測與GM7公開規格審查（2026-11-14）

`mqsim_windows_5cd221f4`：28個manifest檔案重新校驗通過；四組各65,536筆完成。random 256MiB結束於0.366035模擬秒、random 32GiB為0.582306秒，兩組streaming皆0.515833秒。因此後段N/A是因全部在1秒warmup切點前結束，不是後處理漏資料；不能把切點事後降低來宣稱穩態。

| 模式 | 累計平均latency µs | CMT read misses | mapping read計數 |
|---|---:|---:|---:|
| random 256MiB | 178.62 | 7119 | 6443 |
| random 32GiB | 284.24 | 61847 | 61609 |
| streaming 256MiB | 251.81 | 63990 | 3420 |
| streaming 32GiB | 251.81 | 63990 | 3420 |

streaming大量miss並未等於同數量的mapping NAND讀取，支持追查共享等待／mapping讀取合併。這只是計數線索，不是已還原每筆相依關係。兩個streaming都僅讀256MiB，32GiB未走完，仍不作穩態容量對照。既有分段圖見上方本輪自動紀錄。

### 規格來源與可信範圍

本機唯讀sysfs確認：Predator SSD GM7 2TB、firmware GT11cb0c，PCI vendor 0x1dee、device 0x1602。不能僅由此判定NAND型號／die數。

- [Predator官方GM7規格書](https://www.predatorstorage.com/wp-content/uploads/2024/12/PredatorGM7Specifications.pdf)：PCIe4.0x4／NVMe2.0，HMB與SLC cache；這版2TB表列循序讀7200MB/s、隨機讀925K IOPS。官方並註明規格可能變更，這不是本機相同測試條件下的目標。
- [Maxio官方MAP1602公告](https://www.maxio-tech.com/news/11645/12956.html)：MAP1602為4通道、無外置DRAM主控，支援高速NAND介面。結合本機識別，4-channel是比目前8-channel更有公開依據的候選；仍不把產品族資訊等同完整本機BOM。
- 公開規格未充分約束本機每channel的chip／die／plane、page/block geometry、NAND代際、讀取時間與韌體mapping政策。不能把HMB大小直接當MQSim CMT容量，兩者存取路徑也不同。

### 參數調整決策

使用者已授權調整channel等設定，後續可建立4-channel候選，保留舊8-channel作控制。不可只把8改4就宣稱硬體對齊：現有其他幾何固定時容量與總die數會一起減半，會混入平行度及初始化影響。需明列容量／總die是否固定，未知幾何作候選假設，不選最吻合的一組當成規格。

另外現有channel timing換算為 `floor(1000 / Channel_Transfer_Rate) * 2` ns；直接填2400會得到0ns！因此不能將公開MT/s數值直接塞入目前介面。下一步要先審查資料傳輸時序的單位及整數精度，再選擇有非零、可驗證傳輸成本的實作；不能用0ns傳輸製造高吞吐。此次未改ssdconfig.xml，也沒有啟動新實驗。


## Channel傳輸精度修正與受控拓撲對照（2026-11-14）

原公式先把1000/rate轉整數再乘2，令高速設定的傳輸成本歸零；舊宏還會截掉不足兩個transfer unit的尾端。新增 `Channel_Transfer_Time.h`，以bytes與channel width計算完整DDR cycles，再用整數有理式對整筆burst做一次向上取整：`cycles=ceil(bytes/(2*width)); ns=ceil(cycles*2000/rate_MT_s)`。驗證零rate/width、乘法overflow；不對每個cycle單獨取整。事件引擎仍用ns，總burst量化誤差小於1ns。這是既有channel速率語意的精度修正，不是新增時脈引擎。

SSD_Device將設定速率傳給ONFI_Channel_NVDDR2；資料in/out共用修正，command/setup等其他時序不變。直接呼叫原channel constructor且未設定rate的路徑保留原時間API。舊333MT/s也會受精度修正影響：4KiB payload舊12288ns、新12301ns；500MT/s為8192ns；2400MT/s為1707ns。這是payload傳輸時間，不含command/setup，也不是NAND array read latency。不能再要求舊333設定的IOPS逐位完全一致，須使用修正後reference作控制。

`microbench/test_channel_transfer.cpp`測試已離線執行：檢查上述預期值、1／3byte尾端、零長度、零rate拒絕、bytes單調性、提高速率不增加時間。未啟動模擬。2400只是算術驗證，尚未套用到本機候選配置；不同ONFI代際的command/setup仍需另行證據。

### 下一個實驗：只改channel共享程度

`mqsim_window_probe.py`新增 `--profile reference|four_channel` 與 `--requests`：reference為8channel×4chip；four_channel為4channel×8chip，兩者其餘die／plane／block／page設定相同，總chip/die與模型容量不變。workload Channel_IDs與Chip_IDs同步更新。四通道的方向有MAP1602公開資訊支持，但8chip/channel是控制變因安排，不是GM7 BOM；目前仍為舊MLC幾何，不能稱已對齊2TB TLC硬體。

兩組均維持333MT/s、原NAND時序與CMT，只切換拓撲；每組四種地址／範圍案例、每case1,048,576筆＝4GiB，checkpoint分段方式相同。小範圍streaming走16圈，32GiB只走1/8圈，不能拿它宣稱大範圍已穩態。主要觀察後段是否持續漂移、channel共享是否改變mapping命令率與吞吐；若只改絕對數值卻無法解釋地址／範圍趨勢，不把它當校準成功。

使用者執行（共八個case，前一命令失敗就不要繼續）：

```bash
cd /home/emmanuel/projects/MQSim
bash microbench/run_window_probe.sh --profile reference --requests 1048576 &&
bash microbench/run_window_probe.sh --profile four_channel --requests 1048576
```

每次建立獨立結果目錄並自動分析／畫圖到本文件；結果仍由使用者執行後判讀。此步驟不修改根目錄ssdconfig.xml、不重寫舊輸出，不同時改NAND technology、CMT或速率來追曲線。

<!-- calibration-run:85eb14212be1162f:begin -->
## 分段checkpoint實驗：mqsim_windows_389bdf4d

狀態：INCOMPLETE；Not all planned reads completed, or unexpected writes
<!-- calibration-run:85eb14212be1162f:end -->


## 8／4-channel對照中止：389bdf4d

重新校驗20個manifest檔案通過，但本輪為INCOMPLETE，只執行reference的前兩組。256MiB random完成1,048,576筆，模擬時間約5.491秒；32GiB random生成1,048,576筆、實際服務1,048,567筆，差9筆＝36,864bytes。XML Bytes_Transferred_Read=4,294,930,432，與預期4,294,967,296不同，檢查器正確拒絕。串接指令使用&&，因此四通道輪次未開始，不能提出8／4-channel比較結論。

![未完成案例的診斷時間序列](results/mqsim_windows_389bdf4d/diagnostics/incomplete_windows.png)

大範圍random在約0.01秒窗口仍約111k IOPS、283µs，但後段出現顯著慢速與波動，最後一個checkpoint在130.814秒，只記錄1,048,554筆完成；最終XML比它多13筆，因未達下一個period且未全數完成，沒有最後完成checkpoint。不得把缺少的最後窗口補成零或重寫COMPLETE。小範圍後段窗口約18–20萬IOPS，與大範圍明顯不同，但未完成案例不作正式穩態擬合。

這次問題不是四通道造成：它尚未執行。也不能先判定為真機類似的偶發慢速。`Engine.cpp`事件迴圈在event list為空或stop旗標時退出；目前輸出未明列此次退出原因，因此下一步應補退出狀態與未完成request所在層的診斷，檢查host outstanding、mapping待處理集合與TSU／PHY工作是否一致。尚無證據證明9筆的精確遺失位置，不用任意timeout、降低QD或縮短request數來掩蓋。

本次只分析既有資料並繪製標示INCOMPLETE的圖，保留全部原始檔案，未執行新實驗。先處理未排空的正確性問題，再恢復拓撲校準；目前不請使用者重跑相同長測。


## 未排空定位：退出與Host診斷

進一步從389bdf4d原始XML彙總user-read TSU：enqueue=dequeue=1,048,567，與completed一致；不是觀察到9筆user reads仍留在TSU。此聚合證據優先指向TSU之前的路徑，但無法單靠計數確定是host提交、裝置fetch或mapping入口。查程式未見Stop_simulation的呼叫點，仍以新增退出診斷確認，不猜測原因。

已新增不改排程的stderr診斷：Engine退出列印time_ns、pending_events、stop_flag；IO_Flow結果輸出列印generated/completed、host waiting數、software map entry數、SQ head/tail，以及所有非空host pending request的cid、LBA、arrival/enqueue時間。software map可能含空entry，故entry數不可直接當未完成數；逐筆只列非空項目。

既有response log補report時最終snapshot，未完成也保留最後完成數。成功案例可能與完成時snapshot同時間／同完成數，分析器按這兩欄去除重複端點；不補造缺失完成事件。完整編譯、Python語法與diff檢查已通過，尚未執行新模擬。

只由使用者重跑失敗案例，保留原1,048,576請求、seed與設定：

```bash
cd /home/emmanuel/projects/MQSim
bash microbench/run_window_probe.sh --profile reference --requests 1048576 --case RANDOM_UNIFORM_32768MiB
```

若仍少9筆，腳本預期再次標記INCOMPLETE；這是定位實驗，不以成功退出作唯一判準。查看該case stderr.txt中的ENGINE_EXIT、HOST_EXIT與HOST_PENDING，再決定是否需要裝置端fetch／mapping事件追蹤。本輪沒有放寬完成數檢查、沒有修改排程以掩蓋失敗，也不需要重跑其餘七個案例。

<!-- calibration-run:f81ccfb2cea46a6d:begin -->
## 分段checkpoint實驗：mqsim_windows_bc60f285

狀態：INCOMPLETE；Not all planned reads completed, or unexpected writes
<!-- calibration-run:f81ccfb2cea46a6d:end -->


## 單案例重現bc60f285：事件已耗盡，host尚有9筆

本輪manifest重新校驗通過。stderr明列：ENGINE_EXIT time_ns=130815474131、pending_events=0、stop_flag=0；HOST_EXIT generated=1048576、completed=1048567、waiting=0、software_map_entries=9、SQ head=tail=16。九個非空HOST_PENDING：CID 0、18、31、7、17、26、1、16、28；最早arrival為28.616369347秒，最晚129.016265495秒。它們在不同時間累積，不是只在生成器停止後才出現。

因此確定本次是事件耗盡且host仍持有未完成請求，不是Python計算或編譯錯誤；completion驗證應保持失敗。host head=tail是host看到的指標，不足以證明裝置有收到所有SQ entries。尚不能斷言9筆一定留在host、一定卡mapping或一定由channel修正引起。

補充診斷（不改事件／排程）：Host_Interface_NVMe輸出DEVICE_EXIT，包含裝置SQ head/tail/informed、On_the_fly_requests、累計fetched reads／read transactions、waiting與completed-pending數、CQ head/tail；DEVICE_PENDING列出裝置待處理request的CID、LBA、剩餘transaction數與fetch時間。下次可將九筆CID與host清單對照，區分未被fetch與裝置內尚未完成。

仍使用同一條單案例指令重現；每次建立新目錄，不覆蓋bc60f285。若fetched小於generated且裝置無待處理，查NVMe SQ／doorbell／fetch；若裝置仍有pending，沿其transaction查mapping／排程。這是下一步判別條件，不是已確認根因。

<!-- calibration-run:fd7f7f09fa0df8e9:begin -->
## 分段checkpoint實驗：mqsim_windows_cbc14416

狀態：INCOMPLETE；Not all planned reads completed, or unexpected writes
<!-- calibration-run:fd7f7f09fa0df8e9:end -->


## cbc14416：已排除未fetch，定位至裝置內transaction

本輪manifest校驗通過。DEVICE_EXIT fetched_reads=read_transactions=1,048,576，inflight=waiting=9，completed_pending=0；SQ head=tail=informed=16。九筆DEVICE_PENDING的CID與host九筆完全一致，每筆Transaction_list.size()=1。故前輪「未fetch」候選已被此次證據排除，這不是SQ尚有9筆未送達；尚不能僅由pending清單斷定mapping的精確狀態。

來源審查發現：Cached_Mapping_Table::Evict_one_slot無條件淘汰LRU尾端，沒有排除WAITING；mapping完成路徑以Is_slot_reserved_for_lpn_and_waiting決定是否釋放Waiting_unmapped_read_transactions。這形成值得驗證的失去喚醒風險，但目前沒有證據說這九筆必定經過WAITING淘汰。不能因為看見風險就宣稱已找到根因。

新增不改行為的精確診斷：CMT_EVICT_WAITING列出淘汰時刻、stream及LPA；退出時TRANSACTION_PENDING列出CID、LPA、physical_address_determined、mapping_active、mapping episodes／進入時刻、TSU與PHY departure數。可將未完成LPA與淘汰記錄配對；若沒有匹配，需排除此假設，不能任意修改CMT政策。

此前的兩次定位有實質區分：首先確認事件耗盡，接著確認裝置已fetch。尚未修復未排空問題，沒有把慢速結果當成硬體校準資料；不再請使用者跑整套8組比較。新增診斷仍僅需同一個random32GiB案例，保留seed與request數不變。

<!-- calibration-run:c42df9e28f2c677b:begin -->
## 分段checkpoint實驗：mqsim_windows_2c422ae8

狀態：INCOMPLETE；Not all planned reads completed, or unexpected writes
<!-- calibration-run:c42df9e28f2c677b:end -->


## 2c422ae8判讀與修正：GC barrier後的user transaction被刪除（2026-11-14）

本輪stderr的`CMT_EVICT_WAITING`為0次，「WAITING slot被淘汰而失去喚醒」的假設在此重現中不成立。九筆`TRANSACTION_PENDING`的LPA與所屬request的LBA對不上（例如cid 28：LBA 3295792應為LPA 205987，印出3114），其中兩筆為明顯垃圾值；即request的Transaction_list指向已釋放並被重用的記憶體。

根因（由程式碼確認）：`Address_Mapping_Unit_Page_Level::Remove_barrier_for_accessing_lpa`對等在GC barrier後的user transaction只呼叫本單元的`handle_transaction_serviced_signal_from_PHY`，該函式對非MAPPING來源直接return，隨後`delete`。data cache／host interface從未收到完成通知，所屬user request永遠留在裝置。這是上游既有行為，與channel精度修正及觀測欄位無關。

同一份XML顯示純讀工作負載執行了`Total_GC_Executions=2096`、`Issued_Flash_Erase_CMD=2153`、`Issued_Flash_Program_CMD=303102`。來源：讀取未寫入的LPA時`online_create_entry_for_reads`會配置實體頁並把CMT entry標為dirty；preconditioning又把空閒區塊留在GC門檻附近，因此配置即觸發GC。stdout有「initial occupancy could not be satisfied… adjustments」訊息，表示75%佔用被攤到比working set更大的範圍，working set內有未寫入的LPA。後段慢速（整體平均3377µs）來自這些GC的program／erase，不是讀取路徑本身的行為；先前4096與100k筆短測GC為0，所以沒有顯現。

修正：barrier解除後，將等待的讀寫transaction依一般路徑重新`query_cmt`並提交TSU（頁面已搬移，讀取付出一次真實NAND read，不再是零成本完成）。寫入分支採同樣處理，但目前實驗未涵蓋寫入，尚未驗證。另加`FTL_EXIT`stderr診斷：`read_entries_created_online`、`released_from_lpa_barrier`、`gc_executions`。完整編譯通過，尚未執行模擬。

待驗證（使用者執行同一單案例）：預期completed=1,048,576、無HOST_PENDING，`released_from_lpa_barrier`為小量非零。此修正只解決未排空；純讀卻發生GC仍是校準上的混淆因素，需另行處理working set內LPA全數已寫入的初始化，之後才恢復8／4-channel對照。

<!-- calibration-run:666c12879f215d5f:begin -->
## 分段checkpoint實驗：mqsim_windows_c1616df8

狀態：執行完成；warmup切點 1 模擬秒為預先選定診斷設定，不保證已穩態。

| Case | 後段完成數 | 後段IOPS | 後段mean latency µs |
|---|---:|---:|---:|
| RANDOM_UNIFORM_32768MiB | 935524 | 7862.3314828510875 | 4064.6359338007364 |

![分段趨勢](results/mqsim_windows_c1616df8/analysis/windows.png)

原始log時間為模擬時間。每段在完成事件越過period後記錄，非精確等寬窗口；最後段較短，mapping讀取數不可直接視作固定時間速率。latency以該窗口完成的requests為母體，包含跨窗口到達的工作。CMT／mapping為全裝置累計差值，本實驗僅單flow，不與完成數硬配對。startup、跨warmup邊界及最後generated達上限的區間不納入後段彙總；仍應檢查後段是否持續漂移。功能短測不代表穩態。
<!-- calibration-run:666c12879f215d5f:end -->


## 新增裝置參數 GC_Enabled（2026-11-14）

使用者後續研究DiskANN類純讀工作負載，不需要GC。新增可選XML欄位 `<GC_Enabled>false</GC_Enabled>`（Device_Parameter_Set下），未指定時預設true，行為與原本相同；GC／WL程式碼全部保留。

- false時`GC_and_WL_Unit_Page_Level::Check_gc_required`直接返回，不再啟動GC。static wear-leveling只在erase完成後觸發，因此也不會發生。
- 無GC時區塊不會回收：使用者寫入遇到空閒區塊不足時，原本會靜默等待GC；現在改為明確報錯並結束，避免再出現事件耗盡卻未完成的情況。
- 讀取未寫入LPA仍會配置實體頁並產生mapping寫回（program）；關閉GC只移除GC搬移與erase，這部分混淆仍在，可由`FTL_EXIT read_entries_created_online`量測。
- `mqsim_window_probe.py`新增`--no-gc`，寫入該欄位並在plan.json記錄`gc_enabled`。其他腳本尚未加此選項。

完整編譯與Python語法檢查通過，尚未執行模擬。

<!-- calibration-run:75f256420463a2a1:begin -->
## 分段checkpoint實驗：mqsim_windows_59b5f170

狀態：執行完成；warmup切點 1 模擬秒為預先選定診斷設定，不保證已穩態。

| Case | 後段完成數 | 後段IOPS | 後段mean latency µs |
|---|---:|---:|---:|
| RANDOM_UNIFORM_32768MiB | 934671 | 171394.24368188131 | 186.70646640582623 |

![分段趨勢](results/mqsim_windows_59b5f170/analysis/windows.png)

原始log時間為模擬時間。每段在完成事件越過period後記錄，非精確等寬窗口；最後段較短，mapping讀取數不可直接視作固定時間速率。latency以該窗口完成的requests為母體，包含跨窗口到達的工作。CMT／mapping為全裝置累計差值，本實驗僅單flow，不與完成數硬配對。startup、跨warmup邊界及最後generated達上限的區間不納入後段彙總；仍應檢查後段是否持續漂移。功能短測不代表穩態。
<!-- calibration-run:75f256420463a2a1:end -->


## 59b5f170（GC關閉）判讀：mapping寫回從未執行；新增完整working-set預寫（2026-11-14）

本輪完成全部1,048,576筆，`gc_executions=0`、`released_from_lpa_barrier=0`，GC_Enabled=false生效。但`read_entries_created_online=231819`與GC開啟時相同。

分段結果出現模型假象：0–2.2模擬秒約112k IOPS、284µs，每次CMT miss約對應0.94次mapping read；約2.2秒後跳到約188k IOPS、170µs，CMT miss率仍約94.5%，但mapping read降到每query約0.01。

根因（程式碼確認）：`TSU_Priority_OutOfOrder`把mapping寫入放進`MappingWriteTRQueue`，但沒有任何路徑從該queue取出（TSU_OutofOrder與TSU_FLIN也一樣，皆為上游行為）。XML中`Issued_Flash_Program_CMD=0`，而`Issued_Flash_Program_CMD_For_Mapping=12240`實為「產生」數，不是發出數。寫回永遠不完成，`DepartingMappingEntries`只增不減；`request_mapping_entry`對departing中的MVPN視為資料在記憶體，不發flash read。約2.2秒時working set內MVPN幾乎都變成departing，之後miss全部零成本命中。188k平台不可當作模型性能。

來源鏈：preconditioning的75%佔用（約23.4M LPA）大於32GiB working set（約4.19M LPA），觸發「adjustments」改在整個flow範圍隨機選LPA，working set內每頁約75%機率被寫入；未寫入頁被讀時`online_create_entry_for_reads`配置實體頁並把CMT entry標dirty，淘汰時產生寫回。觀測22%讀取打到未寫入頁，符合預期。

修正（已編譯，尚未執行）：

1. `TSU_Priority_OutOfOrder::get_next_write_service_queue`優先回傳`MappingWriteTRQueue`，與讀取端mapping優先一致。寫入仍只在chip沒有可服務讀取時發出。只修了目前使用的scheduler。
2. 新增可選flow欄位`<Precondition_Full_Working_Set>true</Precondition_Full_Working_Set>`（預設false）：在原隨機佔用之外，把working set每一頁補進preconditioning集合，stderr印`PRECONDITION_FULL_WORKING_SET`與補入頁數。working set外仍維持原隨機佔用。對應真機fio讀取的是已寫滿的檔案。
3. `mqsim_window_probe.py`新增`--full-ws`，plan.json記錄`precondition_full_working_set`。

驗證判準：`--no-gc`單獨執行時`Issued_Flash_Program_CMD`應大於0，且不再出現約2.2秒後的188k跳升；`--no-gc --full-ws`時`read_entries_created_online`應為0，mapping寫入應接近0，後段應維持單一平台。

<!-- calibration-run:36495274092f04c1:begin -->
## 分段checkpoint實驗：mqsim_windows_e4185ae3

狀態：執行完成；warmup切點 1 模擬秒為預先選定診斷設定，不保證已穩態。

| Case | 後段完成數 | 後段IOPS | 後段mean latency µs |
|---|---:|---:|---:|
| RANDOM_UNIFORM_32768MiB | 934671 | 171394.24368188131 | 186.70646640582623 |

![分段趨勢](results/mqsim_windows_e4185ae3/analysis/windows.png)

原始log時間為模擬時間。每段在完成事件越過period後記錄，非精確等寬窗口；最後段較短，mapping讀取數不可直接視作固定時間速率。latency以該窗口完成的requests為母體，包含跨窗口到達的工作。CMT／mapping為全裝置累計差值，本實驗僅單flow，不與完成數硬配對。startup、跨warmup邊界及最後generated達上限的區間不納入後段彙總；仍應檢查後段是否持續漂移。功能短測不代表穩態。
<!-- calibration-run:36495274092f04c1:end -->

<!-- calibration-run:6dbcd16f8495306b:begin -->
## 分段checkpoint實驗：mqsim_windows_fe6640f9

狀態：執行完成；warmup切點 1 模擬秒為預先選定診斷設定，不保證已穩態。

| Case | 後段完成數 | 後段IOPS | 後段mean latency µs |
|---|---:|---:|---:|
| RANDOM_UNIFORM_32768MiB | 935848 | 112511.68588318491 | 284.4157797569691 |

![分段趨勢](results/mqsim_windows_fe6640f9/analysis/windows.png)

原始log時間為模擬時間。每段在完成事件越過period後記錄，非精確等寬窗口；最後段較短，mapping讀取數不可直接視作固定時間速率。latency以該窗口完成的requests為母體，包含跨窗口到達的工作。CMT／mapping為全裝置累計差值，本實驗僅單flow，不與完成數硬配對。startup、跨warmup邊界及最後generated達上限的區間不納入後段彙總；仍應檢查後段是否持續漂移。功能短測不代表穩態。
<!-- calibration-run:6dbcd16f8495306b:end -->


## e4185ae3／fe6640f9判讀（2026-11-14）

**fe6640f9（`--no-gc --full-ws`）是第一個乾淨的長測。** 補寫1,048,755頁使32GiB的4,194,304頁全部有資料；`read_entries_created_online=0`、program=0、GC=0。9.32模擬秒內每個時段的窗口IOPS中位數都在111.9k–112.8k，延遲284–286µs，每次CMT查詢約0.94次mapping read，沒有漂移或跳升。這組設定可作後續校準的基礎。

**e4185ae3（只`--no-gc`）與59b5f170完全相同，program仍為0，前次TSU修正無效。** 進一步追查：寫回時建立的merge read傳入`related_write=NULL`，write卻以`RelatedRead=readTR`建立。read完成時處理函式只在`RelatedWrite != NULL`時才清除write的RelatedRead，因此`TSU::transaction_is_ready`永遠視該write未就緒。TSU修正（取出MappingWriteTRQueue）仍是必要的；這是同一路徑上的第二個上游bug。已在`generate_flash_writeback_request_for_mapping_data`設定`readTR->RelatedWrite = writeTR`，編譯通過，尚未執行。

對既有基準的影響：舊24組基準同樣有約22%讀取打到未寫入頁，但跳升約在第25萬筆後才出現，100k筆內未觸及；fe6640f9的QD32為112.6k／284µs，與舊基準QD32（112.5k／284µs）幾乎相同。舊QD曲線大致不受此假象影響，但仍以乾淨設定重跑為準。

`mqsim_calibrate.py prepare`新增`--clean`：GC_Enabled=false、精確32GiB working set（Working_Set_Sectors=67108864）、Precondition_Full_Working_Set=true。

<!-- calibration-run:cfdee96ee2d6c550:begin -->
## 分段checkpoint實驗：mqsim_windows_ed0f4ed5

狀態：執行完成；warmup切點 1 模擬秒為預先選定診斷設定，不保證已穩態。

| Case | 後段完成數 | 後段IOPS | 後段mean latency µs |
|---|---:|---:|---:|
| RANDOM_UNIFORM_32768MiB | 935489 | 103720.52581735626 | 308.52111324986186 |

![分段趨勢](results/mqsim_windows_ed0f4ed5/analysis/windows.png)

原始log時間為模擬時間。每段在完成事件越過period後記錄，非精確等寬窗口；最後段較短，mapping讀取數不可直接視作固定時間速率。latency以該窗口完成的requests為母體，包含跨窗口到達的工作。CMT／mapping為全裝置累計差值，本實驗僅單flow，不與完成數硬配對。startup、跨warmup邊界及最後generated達上限的區間不納入後段彙總；仍應檢查後段是否持續漂移。功能短測不代表穩態。
<!-- calibration-run:cfdee96ee2d6c550:end -->


## ed0f4ed5：mapping寫回修正驗證（2026-11-14）

只`--no-gc`、未完整預寫。全部1,048,576筆完成；mapping寫回產生12,240次，實際發出單平面program 12,194次加multiplane program 23次（每次2筆），合計12,240筆全數執行（前兩輪為0），兩個上游bug的修正一起生效。約2.2秒後不再跳到188k：每次CMT查詢的mapping read維持0.94–1.01，窗口IOPS中位數在81k–113k之間，寫回program造成部分時段下降（例如4–12秒約99k–105k、延遲304–322µs）。這是未完整預寫時的預期代價，不是校準設定；校準與DiskANN研究使用`--full-ws`，不產生寫回。

<!-- calibration-run:7cb28e2ff9a6e360:begin -->
## 執行紀錄：mqsim_calibration_20261113T201301Z_b998d705

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_20261113T201301Z_b998d705`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 2 | 1.958 | 0.801 [0.527, 1.776] | 1.019 | 2.497 [1.126, 3.797] |
| reference | 321 | 4 | 3.771 | 3.049 [1.347, 3.831] | 1.062 | 1.312 [1.044, 2.974] |
| reference | 321 | 8 | 7.009 | 2.070 [1.398, 4.699] | 1.143 | 3.867 [1.703, 5.731] |
| reference | 321 | 16 | 12.107 | 7.254 [2.844, 7.798] | 1.323 | 2.206 [2.053, 5.634] |
| reference | 321 | 32 | 18.152 | 4.224 [2.874, 9.319] | 1.764 | 7.583 [3.435, 11.153] |
| reference | 321 | 64 | 22.351 | 6.860 [2.958, 9.665] | 2.863 | 9.339 [6.625, 21.670] |
| reference | 321 | 128 | 24.212 | 4.410 [2.964, 9.697] | 5.286 | 29.057 [13.208, 43.250] |
| reference | 322 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 322 | 2 | 1.958 | 0.801 [0.527, 1.776] | 1.019 | 2.497 [1.126, 3.797] |
| reference | 322 | 4 | 3.773 | 3.049 [1.347, 3.831] | 1.062 | 1.312 [1.044, 2.974] |
| reference | 322 | 8 | 7.011 | 2.070 [1.398, 4.699] | 1.143 | 3.867 [1.703, 5.731] |
| reference | 322 | 16 | 12.095 | 7.254 [2.844, 7.798] | 1.323 | 2.206 [2.053, 5.634] |
| reference | 322 | 32 | 18.124 | 4.224 [2.874, 9.319] | 1.770 | 7.583 [3.435, 11.153] |
| reference | 322 | 64 | 22.373 | 6.860 [2.958, 9.665] | 2.863 | 9.339 [6.625, 21.670] |
| reference | 322 | 128 | 24.259 | 4.410 [2.964, 9.697] | 5.286 | 29.057 [13.208, 43.250] |
| reference | 323 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 323 | 2 | 1.958 | 0.801 [0.527, 1.776] | 1.019 | 2.497 [1.126, 3.797] |
| reference | 323 | 4 | 3.774 | 3.049 [1.347, 3.831] | 1.062 | 1.312 [1.044, 2.974] |
| reference | 323 | 8 | 7.011 | 2.070 [1.398, 4.699] | 1.143 | 3.867 [1.703, 5.731] |
| reference | 323 | 16 | 12.110 | 7.254 [2.844, 7.798] | 1.323 | 2.206 [2.053, 5.634] |
| reference | 323 | 32 | 18.158 | 4.224 [2.874, 9.319] | 1.764 | 7.583 [3.435, 11.153] |
| reference | 323 | 64 | 22.360 | 6.860 [2.958, 9.665] | 2.863 | 9.339 [6.625, 21.670] |
| reference | 323 | 128 | 24.224 | 4.410 [2.964, 9.697] | 5.292 | 29.057 [13.208, 43.250] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 6194.1 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 321 | 2 | 12127.9 | 4524.0 [4426.6, 4560.8] | 164.0 | 164.0 | 441.6 |
| reference | 321 | 4 | 23361.1 | 11303.9 [7828.9, 21633.5] | 171.0 | 171.0 | 353.7 |
| reference | 321 | 8 | 43414.0 | 11734.8 [11691.6, 12065.7] | 184.0 | 184.0 | 681.5 |
| reference | 321 | 16 | 74992.9 | 23872.9 [18629.0, 44038.0] | 213.0 | 213.0 | 670.0 |
| reference | 321 | 32 | 112438.0 | 23929.9 [23856.0, 24122.9] | 284.0 | 284.0 | 1337.0 |
| reference | 321 | 64 | 138442.4 | 24831.5 [24819.4, 38743.4] | 461.0 | 461.0 | 2577.1 |
| reference | 321 | 128 | 149973.8 | 24900.9 [24883.5, 24906.6] | 851.0 | 851.0 | 5140.0 |
| reference | 322 | 1 | 6190.8 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 322 | 2 | 12121.2 | 4524.0 [4426.6, 4560.8] | 164.0 | 164.0 | 441.6 |
| reference | 322 | 4 | 23358.1 | 11303.9 [7828.9, 21633.5] | 171.0 | 171.0 | 353.7 |
| reference | 322 | 8 | 43405.2 | 11734.8 [11691.6, 12065.7] | 184.0 | 184.0 | 681.5 |
| reference | 322 | 16 | 74876.8 | 23872.9 [18629.0, 44038.0] | 213.0 | 213.0 | 670.0 |
| reference | 322 | 32 | 112200.1 | 23929.9 [23856.0, 24122.9] | 285.0 | 285.0 | 1337.0 |
| reference | 322 | 64 | 138504.2 | 24831.5 [24819.4, 38743.4] | 461.0 | 461.0 | 2577.1 |
| reference | 322 | 128 | 150185.9 | 24900.9 [24883.5, 24906.6] | 851.0 | 851.0 | 5140.0 |
| reference | 323 | 1 | 6193.7 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 323 | 2 | 12126.6 | 4524.0 [4426.6, 4560.8] | 164.0 | 164.0 | 441.6 |
| reference | 323 | 4 | 23374.0 | 11303.9 [7828.9, 21633.5] | 171.0 | 171.0 | 353.7 |
| reference | 323 | 8 | 43425.0 | 11734.8 [11691.6, 12065.7] | 184.0 | 184.0 | 681.5 |
| reference | 323 | 16 | 75007.6 | 23872.9 [18629.0, 44038.0] | 213.0 | 213.0 | 670.0 |
| reference | 323 | 32 | 112463.8 | 23929.9 [23856.0, 24122.9] | 284.0 | 284.0 | 1337.0 |
| reference | 323 | 64 | 138491.2 | 24831.5 [24819.4, 38743.4] | 461.0 | 461.0 | 2577.1 |
| reference | 323 | 128 | 150038.2 | 24900.9 [24883.5, 24906.6] | 852.0 | 852.0 | 5140.0 |

### 本輪限制

- Simulator working set approx 33.3312 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.

### 趨勢圖

細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。

![reference：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261113T201301Z_b998d705/analysis/figures/trend_v0_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![reference：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261113T201301Z_b998d705/analysis/figures/trend_v0_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。
<!-- calibration-run:7cb28e2ff9a6e360:end -->

## 乾淨基準 b998d705 解讀與真機雙狀態發現（2026-11-14）

### 模擬端檢查

24/24 case 完成：`completed=100000`，`gc_executions=0`，`read_entries_created_online=0`，`released_from_lpa_barrier=0`，working set 全部預先寫入（`lpas=4194304`）。三個 seed 的 IOPS 差異 <0.3%，模型本身已穩定，可作為之後拓撲／NAND 候選的比較基準。

| QD | 1 | 4 | 16 | 32 | 64 | 128 |
|---|---:|---:|---:|---:|---:|---:|
| sim IOPS | 6.19k | 23.4k | 75.0k | 112.4k | 138.5k | 150.0k |
| sim 平均延遲 (µs) | 161 | 171 | 213 | 284 | 461 | 851 |

### 真機逐點中位數混了兩種裝置狀態

重新讀 `mqsim_long_20261112T153235Z_c53192bf` 每個 run 的每秒 IOPS log（`iops_iops.1.log`，`log_avg_msec=1000`）。同一個 QD 內，每秒 IOPS 呈現雙峰，而且狀態會在 run 中途切換（例如 r3_qd16 在第 33–48 秒從 19k 跳到 126k，再掉回 19k）。依每個 QD 對 log(IOPS) 做 2-means 分群，延遲用 Little's law 換算（QD/IOPS）：

| QD | 慢狀態秒數 | 慢 IOPS 中位數 | 慢延遲 µs | 快狀態秒數 | 快 IOPS 中位數 | 快延遲 µs | sim IOPS |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 108 | 2,392 | 418 | 72 | 10,048 | 100 | 6,193 |
| 2 | 180 | 4,128 | 484 | 0 | – | – | 12,125 |
| 4 | 149 | 7,375 | 542 | 31 | 42,280 | 95 | 23,364 |
| 8 | 180 | 11,508 | 695 | 0 | – | – | 43,415 |
| 16 | 161 | 18,679 | 857 | 19 | 125,536 | 127 | 74,959 |
| 32 | 180 | 24,024 | 1,332 | 0 | – | – | 112,367 |
| 64 | 175 | 24,843 | 2,576 | 5 | 212,144 | 302 | 138,479 |
| 128 | 180 | 24,931 | 5,134 | 0 | – | – | 150,066 |

- 慢狀態（約 91% 的秒數）：QD1 就要 418µs，QD≥32 硬上限約 25k IOPS。這一段決定了先前的「25k 天花板」與「QD2 比 QD1 慢」，後者是 QD1 中位數混到快狀態秒數所致。
- 快狀態（約 9%）：QD1 100µs、QD4 仍 95µs、QD16 127µs、QD64 約 212k IOPS，是典型隨 NAND 平行度擴張的曲線，形狀與目前模型接近，絕對值比模型快約 1.5 倍。
- 快狀態持續時間隨負載增加而縮短（QD1 約 25–45 秒，QD64 約 5 秒）。可能的解釋：溫度／功耗限速，或背景工作（根目錄也在這顆碟上，使用率 89%，可能有 SLC 折疊／GC）。目前無法區分。
- 量測當下沒有記錄溫度、thermal 計數或主機對同一顆碟的寫入。2026-11-14 閒置時 Composite 溫度 26.85°C，warning 89.85°C、critical 94.85°C（hwmon）。

### 對校準的影響

- 逐點中位數不是單一裝置狀態下的 QD 曲線，不應直接拿來調 NAND 參數。拿模型去追 25k 天花板，等於用 NAND 拓撲去解釋一個可能來自控制器狀態的限制。
- 對 NAND transaction batching 的研究來說，兩個狀態的 knee 完全不同（慢狀態約 QD16–32，快狀態 QD64 以上仍在長），分批結論會依狀態而異。需要先知道哪個狀態代表 DiskANN 實際執行時的裝置。
- 新增 `microbench/state_probe.sh` 與 `microbench/analyze_state_probe.py`：在 fio 量測期間以 1 Hz 記錄溫度（hwmon）、`/proc/diskstats` 的主機讀寫，並在每個階段前後快照 `nvme smart-log`（thermal transition 計數與時間）、電源狀態、HMB、APST。
