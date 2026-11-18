# =====================================================================================
#  Project AGAP: Flood Risk of Households in Santa Cruz, Laguna
#  CSST 101 - Statistical Analysis in R (final script, Weeks 1 to 7)
#  CS 3-B, Group 9
#
#  Research question:
#    Which household, terrain, flood-hazard and rainfall factors are associated with
#    higher flood risk levels among households in Santa Cruz, Laguna?
#
#  How to run
#    RStudio : put this file next to AGAP_Colab_Data.zip, set the working directory to
#              this file's folder (Session > Set Working Directory > To Source File
#              Location) and click Source.
#    Colab   : open AGAP_CSST101_Analysis_R.ipynb (it uses the R runtime), upload
#              AGAP_Colab_Data.zip in the Files panel, then Runtime > Run all.
#  Only base R is used (no packages to install). A full run takes 1 to 2 minutes.
#  Every table and chart is written to the "outputs" folder.
#
#  Contents
#    WEEK 1  Set-up: find and load the data
#    WEEK 2  Import the raw file; types, data dictionary, missing values, duplicates
#    WEEK 3  Cleaning, household-level table, descriptive statistics, Charts 1-3
#    WEEK 4  Chi-square, Kruskal-Wallis, Spearman, logistic regression, Charts 4-5
#    WEEK 5  Verification, sensitivity check without 2020, improved Charts 1, 3 and 6
#
#  Key objects
#    raw          raw dataset as imported (241,200 household-event records)
#    clean        published clean dataset (232,110 records, one per household and rain event)
#    hh           household-level table (7,737 rows, one per household); the unit of analysis
#                 for household traits. MaxRisk = highest risk level reached over 30 events,
#                 HighEvents = number of events rated High, AnyHigh = 1 if High at least once
#    events       the 60 rain events, with PctHigh = % of households rated High in that event
#    chi_tab, kw_tab, sp_tab, or_tab   result tables of the Week 4 tests
#    fit          the logistic regression model
#    verify       Week 5 comparison of R results with the independent Python check
# =====================================================================================


# -------------------------------------------------------------------------------------
# WEEK 1. Project set-up and first look at the data
# -------------------------------------------------------------------------------------
cat(R.version.string, "\n")
options(width = 120, scipen = 6)
OUT_DIR <- "outputs"
dir.create(OUT_DIR, showWarnings = FALSE)

# Locate the data: an uploaded zip, an already unzipped "data" folder, or the project folder
if (!file.exists(file.path("data", "agap_noah_dataset.csv")) && file.exists("AGAP_Colab_Data.zip")) {
  unzip("AGAP_Colab_Data.zip", exdir = "data")
}
DATA_DIR <- if (file.exists(file.path("data", "agap_noah_dataset.csv"))) "data" else
  file.path("..", "..", "AGAP_NOAH_Dataset")
if (!file.exists(file.path(DATA_DIR, "agap_noah_dataset.csv"))) {
  stop("Data not found. Put AGAP_Colab_Data.zip in the working directory: ", getwd())
}
cat("Data folder:", DATA_DIR, "\n")

# na.strings = "" : only empty cells are missing. "None" is a real flood class
# (the house is outside every NOAH flood zone) and must stay a value.
read_data <- function(name) {
  read.csv(file.path(DATA_DIR, name), na.strings = "", stringsAsFactors = FALSE)
}

clean <- read_data("agap_noah_dataset.csv")
cat("Clean dataset:", nrow(clean), "rows x", ncol(clean), "columns\n")
LEVELS <- c("Low", "Medium", "High")
print(table(RiskLevel = factor(clean$RiskLevel, levels = LEVELS)))


# -------------------------------------------------------------------------------------
# WEEK 2. Importation and exploration of the raw data
# -------------------------------------------------------------------------------------
raw <- read_data("agap_noah_dataset_raw.csv")
cat("\nRaw dataset:", nrow(raw), "rows x", ncol(raw), "columns\n")
str(raw)

# Data types
types <- sapply(raw, function(x) class(x)[1])
print(table(types))

# Data dictionary: every column must be documented
dict <- read_data("data_dictionary.csv")
stopifnot(identical(dict$Column, names(raw)))
print(dict[, c("Column", "Role", "Type", "Unit", "Origin")], right = FALSE)

# Missing values
missing <- colSums(is.na(raw))
missing_tab <- data.frame(Column = names(missing)[missing > 0], Missing = missing[missing > 0],
                          Percent = round(100 * missing[missing > 0] / nrow(raw), 2), row.names = NULL)
print(missing_tab)

# Duplicates
n_dup_rows <- sum(duplicated(raw))
n_dup_pairs <- sum(duplicated(raw[, c("HouseholdID", "EventDate")]))
cat("Exact duplicate rows:", n_dup_rows, "\n")
cat("Repeated household-event pairs:", n_dup_pairs, "\n")
cat("Households:", length(unique(raw$HouseholdID)), " Barangays:", length(unique(raw$Barangay)),
    " Rain events:", length(unique(raw$EventDate)), "\n")

# Levels of the categorical columns
cat_cols <- names(raw)[types == "character" & !names(raw) %in% c("HouseholdID", "EventDate", "Barangay")]
for (col in cat_cols) {
  cat("\n", col, ":\n", sep = "")
  print(table(raw[[col]], useNA = "ifany"))
}
write.csv(missing_tab, file.path(OUT_DIR, "w2_missing_values.csv"), row.names = FALSE)


# -------------------------------------------------------------------------------------
# WEEK 3. Cleaning, transformation, descriptive statistics and charts
# -------------------------------------------------------------------------------------

# Step 1: remove exact duplicate rows
step1 <- raw[!duplicated(raw), ]

# Step 2: remove households with any missing value (household details feed the risk
# label, so filling them in would describe a household that does not exist)
incomplete <- unique(step1$HouseholdID[!complete.cases(step1)])
step2 <- step1[!(step1$HouseholdID %in% incomplete), ]

# Step 3: recompute wrong labels from the documented rule (risk score cut-offs 15 and 40)
audit <- read_data("label_audit.csv")
step2$RiskScore <- audit$RiskScore[match(paste(step2$HouseholdID, step2$EventDate),
                                         paste(audit$HouseholdID, audit$EventDate))]
stopifnot(!anyNA(step2$RiskScore))
rule_label <- as.character(cut(step2$RiskScore, c(-Inf, 15, 40, Inf), right = FALSE, labels = LEVELS))
labels_fixed <- sum(rule_label != step2$RiskLevel)
step2$RiskLevel <- rule_label
step2$RiskScore <- NULL
rm(audit)

cleaning_log <- data.frame(
  Step = c("Raw file", "After removing duplicate rows", "After removing incomplete households",
           "After correcting labels"),
  Rows = c(nrow(raw), nrow(step1), nrow(step2), nrow(step2)),
  Households = c(length(unique(raw$HouseholdID)), length(unique(step1$HouseholdID)),
                 length(unique(step2$HouseholdID)), length(unique(step2$HouseholdID))))
print(cleaning_log)
cat("Incomplete households removed:", length(incomplete), "  Labels corrected:", labels_fixed, "\n")

# Check against the published clean file (same rows, same order)
stopifnot(nrow(step2) == nrow(clean),
          identical(step2$HouseholdID, clean$HouseholdID),
          identical(step2$EventDate, clean$EventDate))
label_match <- sum(step2$RiskLevel == clean$RiskLevel)
cat("Labels identical to the published clean file:", label_match, "of", nrow(clean), "\n")
# The one difference is a record whose stored risk score is rounded to exactly 40.0,
# the Medium/High cut-off. The published file is used from here on.
write.csv(cleaning_log, file.path(OUT_DIR, "w3_cleaning_log.csv"), row.names = FALSE)
rm(raw, step1, step2)

# Transformation: event type, ordered factors, numeric risk code
events <- read_data("rainfall_events.csv")
clean$EventType <- factor(events$EventType[match(clean$EventDate, events$EventDate)],
                          levels = c("Light", "Moderate", "Heavy", "Extreme"))
stopifnot(!anyNA(clean$EventType))
clean$RiskLevel <- factor(clean$RiskLevel, levels = LEVELS)
clean$RiskCode <- as.integer(clean$RiskLevel) - 1L          # 0 = Low, 1 = Medium, 2 = High

# Household-level table (one row per household). Each household appears 30 times, once per
# rain event, so household traits are analysed on this table to avoid counting a house 30 times.
stopifnot(all(table(clean$HouseholdID) == 30))
hh_cols <- c("HouseholdID", "Barangay", "Age", "HousingMaterial", "HouseMembers", "ChildrenCount",
             "ElderCount", "PWDCount", "ElevationMeters", "SlopeDegrees", "DistanceToWaterwayM",
             "LocationType", "LandCoverType", "HistoricalWaterOccurrencePct", "HouseFloodHazard100yr",
             "MGBFloodSusceptibility")
hh <- clean[!duplicated(clean$HouseholdID), hh_cols]
rownames(hh) <- NULL
high_events <- tapply(clean$RiskCode == 2L, clean$HouseholdID, sum)
max_code <- tapply(clean$RiskCode, clean$HouseholdID, max)
hh$HighEvents <- as.integer(high_events[hh$HouseholdID])
hh$MaxRisk <- factor(LEVELS[max_code[hh$HouseholdID] + 1], levels = LEVELS)
hh$AnyHigh <- as.integer(hh$HighEvents > 0)
hh$Dependents <- hh$ChildrenCount + hh$ElderCount + hh$PWDCount
hh$HousingMaterial <- factor(hh$HousingMaterial, levels = c("Concrete", "SemiConcrete", "LightMaterials"))
hh$HouseFloodHazard100yr <- factor(hh$HouseFloodHazard100yr, levels = c("None", "Low", "Medium", "High"))
hh$MGBFloodSusceptibility <- factor(hh$MGBFloodSusceptibility, levels = c("NotMapped", "Moderate", "High"))
hh$LocationType <- factor(hh$LocationType, levels = c("Riverside", "NearWaterway", "Inland"))
cat("Household table:", nrow(hh), "households\n")
write.csv(hh, file.path(OUT_DIR, "w3_household_summary.csv"), row.names = FALSE)

# Descriptive statistics
describe <- function(x) {
  c(n = length(x), Mean = mean(x), SD = sd(x), Min = min(x), Q1 = unname(quantile(x, 0.25)),
    Median = median(x), Q3 = unname(quantile(x, 0.75)), Max = max(x))
}
num_hh <- c("Age", "HouseMembers", "ChildrenCount", "ElderCount", "PWDCount", "Dependents",
            "ElevationMeters", "SlopeDegrees", "DistanceToWaterwayM", "HistoricalWaterOccurrencePct")
desc_hh <- round(t(sapply(hh[num_hh], describe)), 2)
cat("\nHouseholds (n = 7,737):\n"); print(desc_hh)

desc_ev <- round(t(sapply(events[c("Rainfall24hMm", "Rainfall3DayMm", "Rainfall7DayMm")], describe)), 2)
cat("\nRain events (n = 60):\n"); print(desc_ev)
print(aggregate(Rainfall24hMm ~ EventType, data = events, FUN = function(x) round(c(Min = min(x), Median = median(x), Max = max(x)), 1)))

freq <- function(x) {
  t <- table(x)
  data.frame(Level = names(t), Count = as.vector(t), Percent = round(100 * as.vector(t) / sum(t), 1))
}
cat("\nRisk level, all 232,110 household-events:\n"); print(freq(clean$RiskLevel))
cat("\nHighest risk level reached by each household (30 events):\n"); print(freq(hh$MaxRisk))
for (v in c("HousingMaterial", "LocationType", "HouseFloodHazard100yr", "MGBFloodSusceptibility")) {
  cat("\n", v, " (households):\n", sep = ""); print(freq(hh[[v]]))
}
risk_by_type <- round(100 * prop.table(table(clean$EventType, clean$RiskLevel), 1), 1)
cat("\nRisk level by event type (row %):\n"); print(risk_by_type)

write.csv(desc_hh, file.path(OUT_DIR, "w3_descriptive_households.csv"))
write.csv(desc_ev, file.path(OUT_DIR, "w3_descriptive_rain_events.csv"))
write.csv(as.data.frame.matrix(risk_by_type), file.path(OUT_DIR, "w3_risk_by_event_type_pct.csv"))

# Charts. PNG files go to the outputs folder; every chart is also shown on screen in RStudio/Colab.
save_chart <- function(file, width = 7, height = 4.5, draw) {
  if (capabilities("png")) png(file.path(OUT_DIR, file), width = width, height = height, units = "in", res = 150)
  else pdf(file.path(OUT_DIR, sub("png$", "pdf", file)), width = width, height = height)
  draw()
  invisible(dev.off())
  draw()
}

# Week 3, Chart 1: risk level of all household-events by rain event type (counts)
save_chart("w3_chart1_risk_by_event_type.png", draw = function() {
  tab <- table(clean$RiskLevel, clean$EventType)
  par(mar = c(5, 5, 4, 2))
  barplot(tab / 1000, beside = TRUE, legend.text = LEVELS, args.legend = list(x = "topleft", title = "Risk level"),
          main = "Risk level of household-events by rain event type",
          xlab = "Rain event type", ylab = "Household-events (thousands)", las = 1)
})

# Week 3, Chart 2: elevation of the house by the highest risk level it reached
save_chart("w3_chart2_elevation_by_highest_risk.png", draw = function() {
  par(mar = c(5, 5, 4, 2))
  boxplot(ElevationMeters ~ MaxRisk, data = hh, outline = FALSE, las = 1,
          main = "Ground elevation by the household's highest risk level",
          xlab = "Highest risk level over 30 rain events", ylab = "Elevation (m above sea level)")
})

# Week 3, Chart 3: highest risk level by housing material (counts)
save_chart("w3_chart3_highest_risk_by_housing.png", draw = function() {
  tab <- table(hh$MaxRisk, hh$HousingMaterial)
  par(mar = c(5, 5, 4, 2))
  barplot(tab, beside = TRUE, legend.text = LEVELS, args.legend = list(x = "topright", title = "Highest risk"),
          main = "Households' highest risk level by housing material",
          xlab = "Housing material", ylab = "Number of households", las = 1)
})


# -------------------------------------------------------------------------------------
# WEEK 4. Statistical analysis and interpretation
# -------------------------------------------------------------------------------------
# Unit of analysis: the household (n = 7,737) for household traits, and the rain event
# (n = 60) for rainfall. Records of the same household are not independent, so tests on all
# 232,110 records would overstate the evidence. With large samples almost every p-value is tiny,
# so the size of each effect (Cramer's V, epsilon squared, odds ratio) is reported as well.

# (a) Chi-square tests of independence: household trait vs highest risk level
chi_rows <- list()
for (v in c("HousingMaterial", "LocationType", "HouseFloodHazard100yr", "MGBFloodSusceptibility")) {
  tab <- table(hh[[v]], hh$MaxRisk)
  ct <- chisq.test(tab, correct = FALSE)
  chi_rows[[v]] <- data.frame(Variable = v, ChiSquare = unname(ct$statistic), df = unname(ct$parameter),
                              p = ct$p.value, CramersV = sqrt(unname(ct$statistic) / (sum(tab) * (min(dim(tab)) - 1))),
                              MinExpected = min(ct$expected))
  cat("\n", v, " x highest risk level (row %):\n", sep = "")
  print(round(100 * prop.table(tab, 1), 1))
}
chi_tab <- do.call(rbind, chi_rows)
rownames(chi_tab) <- NULL
cat("\nChi-square tests:\n"); print(chi_tab, digits = 4)

# (b) Kruskal-Wallis tests: numeric trait across the three highest-risk groups
kw_rows <- list()
for (v in c("ElevationMeters", "DistanceToWaterwayM", "Dependents")) {
  kt <- kruskal.test(hh[[v]] ~ hh$MaxRisk)
  med <- tapply(hh[[v]], hh$MaxRisk, median)
  kw_rows[[v]] <- data.frame(Variable = v, Median_Low = unname(med["Low"]), Median_Medium = unname(med["Medium"]),
                             Median_High = unname(med["High"]), H = unname(kt$statistic), df = unname(kt$parameter),
                             p = kt$p.value, EpsilonSq = unname(kt$statistic) / (nrow(hh) - 1))
}
kw_tab <- do.call(rbind, kw_rows)
rownames(kw_tab) <- NULL
cat("\nKruskal-Wallis tests:\n"); print(kw_tab, digits = 4)

# (c) Spearman correlations
sp_elev <- cor.test(hh$ElevationMeters, hh$HighEvents, method = "spearman", exact = FALSE)
sp_dist <- cor.test(hh$DistanceToWaterwayM, hh$HighEvents, method = "spearman", exact = FALSE)
ev_high <- tapply(clean$RiskCode == 2L, clean$EventDate, mean)
events$PctHigh <- 100 * as.vector(ev_high[events$EventDate])
sp_rain <- cor.test(events$Rainfall24hMm, events$PctHigh, method = "spearman", exact = FALSE)
sp_tab <- data.frame(
  Pair = c("Elevation vs number of High events (households)", "Distance to waterway vs number of High events (households)",
           "24-hour rainfall vs % of households High (60 events)"),
  n = c(nrow(hh), nrow(hh), nrow(events)),
  rho = c(sp_elev$estimate, sp_dist$estimate, sp_rain$estimate),
  p = c(sp_elev$p.value, sp_dist$p.value, sp_rain$p.value))
cat("\nSpearman correlations:\n"); print(sp_tab, digits = 4)

# (d) Logistic regression: which traits raise the odds that a household is High risk in at
# least one of its 30 rain events? Elevation per 5 m, distance per 100 m.
fit <- glm(AnyHigh ~ HousingMaterial + HouseFloodHazard100yr + MGBFloodSusceptibility +
             I(ElevationMeters / 5) + I(DistanceToWaterwayM / 100) + Dependents,
           data = hh, family = binomial)
print(summary(fit))
ci <- confint.default(fit)                                     # Wald 95% confidence intervals
or_tab <- data.frame(Term = names(coef(fit)), OddsRatio = exp(coef(fit)), Lower95 = exp(ci[, 1]),
                     Upper95 = exp(ci[, 2]), p = summary(fit)$coefficients[, 4], row.names = NULL)
pseudo_r2 <- 1 - fit$deviance / fit$null.deviance
prob <- fitted(fit)
n1 <- sum(hh$AnyHigh == 1); n0 <- sum(hh$AnyHigh == 0)
auc <- (sum(rank(prob)[hh$AnyHigh == 1]) - n1 * (n1 + 1) / 2) / (n1 * n0)
cat("\nOdds ratios:\n"); print(or_tab, digits = 4)
cat("Households High in at least one event:", n1, "of", nrow(hh), sprintf("(%.1f%%)\n", 100 * n1 / nrow(hh)))
cat(sprintf("McFadden pseudo R-squared: %.3f   AUC: %.3f\n", pseudo_r2, auc))

write.csv(chi_tab, file.path(OUT_DIR, "w4_chi_square_tests.csv"), row.names = FALSE)
write.csv(kw_tab, file.path(OUT_DIR, "w4_kruskal_wallis_tests.csv"), row.names = FALSE)
write.csv(sp_tab, file.path(OUT_DIR, "w4_spearman_correlations.csv"), row.names = FALSE)
write.csv(or_tab, file.path(OUT_DIR, "w4_logistic_odds_ratios.csv"), row.names = FALSE)
write.csv(events, file.path(OUT_DIR, "w4_rain_events_pct_high.csv"), row.names = FALSE)

# Week 4, Chart 4: rainfall of each event vs share of households rated High
TYPE_COL <- c(Light = "#56B4E9", Moderate = "#009E73", Heavy = "#E69F00", Extreme = "#D55E00")
save_chart("w4_chart4_rainfall_vs_high_share.png", draw = function() {
  par(mar = c(5, 5, 4, 2))
  plot(events$Rainfall24hMm, events$PctHigh, pch = 19, col = TYPE_COL[events$EventType], las = 1,
       xlim = c(0, 240), ylim = c(0, 45),
       main = "Event rainfall vs share of households at High risk",
       xlab = "24-hour rainfall of the event (mm)", ylab = "Households rated High (%)")
  mtext(sprintf("Each point is one of the 60 rain events. Spearman rho = %.2f", sp_rain$estimate),
        side = 3, line = 0.3, cex = 0.85)
  abline(v = c(132.6, 186.2, 230.4), lty = 2, col = "grey40")
  text(c(132.6, 186.2, 230.4), 44, c("5-yr", "25-yr", "100-yr"), pos = 2, cex = 0.8, col = "grey30")
  legend("topleft", legend = names(TYPE_COL), col = TYPE_COL, pch = 19, title = "Event type", bty = "n")
})

# Week 4, Chart 5: odds ratios with 95% confidence intervals (log scale)
save_chart("w4_chart5_odds_ratios.png", width = 7.5, height = 5, draw = function() {
  o <- or_tab[-1, ]
  labels <- c("Semi-concrete (vs concrete)", "Light materials (vs concrete)", "NOAH Low (vs none)",
              "NOAH Medium (vs none)", "NOAH High (vs none)", "MGB Moderate (vs not mapped)",
              "MGB High (vs not mapped)", "Elevation, per 5 m", "Distance to waterway, per 100 m",
              "Dependents, per person")
  y <- rev(seq_len(nrow(o)))
  par(mar = c(5, 15, 4, 2))
  plot(o$OddsRatio, y, log = "x", pch = 19, yaxt = "n", ylab = "", xlim = range(c(o$Lower95, o$Upper95)),
       main = "Odds of being High risk in at least one event", cex.main = 1.05,
       xlab = "Odds ratio (log scale) with 95% CI")
  segments(o$Lower95, y, o$Upper95, y)
  abline(v = 1, lty = 2, col = "grey40")
  axis(2, at = y, labels = labels, las = 1, cex.axis = 0.85)
})


# -------------------------------------------------------------------------------------
# WEEK 5. Results validation and analytical improvement
# -------------------------------------------------------------------------------------

# (a) Recompute a chi-square statistic by hand: sum of (observed - expected)^2 / expected
tab <- table(hh$MGBFloodSusceptibility, hh$MaxRisk)
expected <- outer(rowSums(tab), colSums(tab)) / sum(tab)
chi_by_hand <- sum((tab - expected)^2 / expected)
cat("\nChi-square (MGB) by hand:", round(chi_by_hand, 4), " chisq.test:", round(chi_tab$ChiSquare[4], 4), "\n")
stopifnot(abs(chi_by_hand - chi_tab$ChiSquare[4]) < 1e-6)

# (b) Totals and counts against the dataset card
stopifnot(nrow(clean) == 232110, nrow(hh) == 7737, sum(table(hh$MaxRisk)) == nrow(hh),
          identical(as.vector(table(clean$RiskLevel)), c(156037L, 56564L, 19509L)))

# (c) Cross-check against values computed independently in Python with the same methods
expected_values <- c(
  labels_fixed = 1181, incomplete_households = 263, duplicate_rows = 1200,
  households_max_high = 2819, any_high_pct = 36.4353,
  chi_housing = 608.6093, V_housing = 0.1983, chi_noah100 = 2177.3043, V_noah100 = 0.3751,
  chi_mgb = 3953.0394, V_mgb = 0.5054, chi_location = 297.5828, V_location = 0.1387,
  H_elevation = 2022.9133, eps2_elevation = 0.2615, H_distance = 311.0775, eps2_distance = 0.0402,
  rho_elevation = -0.4632, rho_distance = -0.2243, rho_rain = 0.9127,
  OR_light = 139.9193, OR_mgb_high = 6715.3729, auc = 0.9706, pseudo_r2 = 0.6723)
r_values <- c(
  labels_fixed = labels_fixed, incomplete_households = length(incomplete), duplicate_rows = n_dup_rows,
  households_max_high = sum(hh$MaxRisk == "High"), any_high_pct = 100 * mean(hh$AnyHigh),
  chi_housing = chi_tab$ChiSquare[1], V_housing = chi_tab$CramersV[1],
  chi_noah100 = chi_tab$ChiSquare[3], V_noah100 = chi_tab$CramersV[3],
  chi_mgb = chi_tab$ChiSquare[4], V_mgb = chi_tab$CramersV[4],
  chi_location = chi_tab$ChiSquare[2], V_location = chi_tab$CramersV[2],
  H_elevation = kw_tab$H[1], eps2_elevation = kw_tab$EpsilonSq[1],
  H_distance = kw_tab$H[2], eps2_distance = kw_tab$EpsilonSq[2],
  rho_elevation = unname(sp_elev$estimate), rho_distance = unname(sp_dist$estimate), rho_rain = unname(sp_rain$estimate),
  OR_light = or_tab$OddsRatio[or_tab$Term == "HousingMaterialLightMaterials"],
  OR_mgb_high = or_tab$OddsRatio[or_tab$Term == "MGBFloodSusceptibilityHigh"], auc = auc, pseudo_r2 = pseudo_r2)
# Python values are rounded to 4 decimals, so a difference of up to 0.00005 is still a match
verify <- data.frame(Check = names(expected_values), Expected = expected_values, R = round(r_values, 4),
                     Match = abs(r_values - expected_values) <= 5e-5 + 1e-6 * abs(expected_values),
                     row.names = NULL)
print(verify)
cat(sum(verify$Match), "of", nrow(verify), "values match the independent cross-check.\n")
write.csv(verify, file.path(OUT_DIR, "w5_verification.csv"), row.names = FALSE)

# (d) Sensitivity check: NASA POWER rainfall for 2020 is doubtful (far above ERA5), and
# 10 of the 60 events are from 2020. Repeat the main results without them.
no2020 <- substr(events$EventDate, 1, 4) != "2020"
sp_rain_no2020 <- cor.test(events$Rainfall24hMm[no2020], events$PctHigh[no2020], method = "spearman", exact = FALSE)
keep <- substr(clean$EventDate, 1, 4) != "2020"
max_no2020 <- tapply(clean$RiskCode[keep], clean$HouseholdID[keep], max)
hh$MaxRiskNo2020 <- factor(LEVELS[max_no2020[hh$HouseholdID] + 1], levels = LEVELS)
ct_no2020 <- chisq.test(table(hh$MGBFloodSusceptibility, hh$MaxRiskNo2020), correct = FALSE)
V_no2020 <- sqrt(unname(ct_no2020$statistic) / (nrow(hh) * 2))
sens_tab <- data.frame(
  Result = c("Spearman rho, rainfall vs % High", "Cramer's V, MGB vs highest risk",
             "Households reaching High at least once (%)"),
  AllEvents = c(unname(sp_rain$estimate), chi_tab$CramersV[4], 100 * mean(hh$MaxRisk == "High")),
  Without2020 = c(unname(sp_rain_no2020$estimate), V_no2020, 100 * mean(hh$MaxRiskNo2020 == "High")))
cat("\nSensitivity check without the 10 events from 2020:\n"); print(sens_tab, digits = 4)
cat("Events left:", sum(no2020), "  Records left:", sum(keep), "\n")
write.csv(sens_tab, file.path(OUT_DIR, "w5_sensitivity_without_2020.csv"), row.names = FALSE)

# (e) Improved charts: percentages instead of counts, colour-blind-safe colours,
# sample sizes in the labels, sorted categories
RISK_COL <- c(Low = "#56B4E9", Medium = "#E69F00", High = "#D55E00")

save_chart("w5_chart1_risk_by_event_type_pct.png", draw = function() {
  pct <- 100 * prop.table(table(clean$RiskLevel, clean$EventType), 2)
  n <- table(clean$EventType)
  par(mar = c(5, 5, 4, 8), xpd = TRUE)
  b <- barplot(pct, col = RISK_COL, las = 1, ylim = c(0, 100),
               names.arg = paste0(colnames(pct), "\n(n = ", format(as.vector(n), big.mark = ","), ")"),
               main = "Risk level by rain event type", xlab = "", ylab = "Share of household-events (%)")
  mid <- apply(pct, 2, function(p) cumsum(p) - p / 2)
  for (j in seq_along(b)) for (i in 1:3) if (pct[i, j] >= 6) text(b[j], mid[i, j], sprintf("%.0f%%", pct[i, j]), cex = 0.8)
  legend("topright", inset = c(-0.2, 0), legend = rev(LEVELS), fill = rev(RISK_COL), title = "Risk level", bty = "n")
})

save_chart("w5_chart3_highest_risk_by_housing_pct.png", draw = function() {
  pct <- 100 * prop.table(table(hh$MaxRisk, hh$HousingMaterial), 2)
  n <- table(hh$HousingMaterial)
  par(mar = c(5, 5, 4, 8), xpd = TRUE)
  b <- barplot(pct, col = RISK_COL, las = 1, ylim = c(0, 100),
               names.arg = paste0(c("Concrete", "Semi-concrete", "Light materials"), "\n(n = ",
                                  format(as.vector(n), big.mark = ","), ")"),
               main = "Highest risk level reached, by housing material", ylab = "Share of households (%)")
  mid <- apply(pct, 2, function(p) cumsum(p) - p / 2)
  for (j in seq_along(b)) for (i in 1:3) text(b[j], mid[i, j], sprintf("%.0f%%", pct[i, j]), cex = 0.8)
  legend("topright", inset = c(-0.22, 0), legend = rev(LEVELS), fill = rev(RISK_COL), title = "Highest risk", bty = "n")
})

save_chart("w5_chart6_barangay_high_share.png", width = 7.5, height = 6.5, draw = function() {
  share <- sort(100 * tapply(hh$AnyHigh, hh$Barangay, mean))
  n <- table(hh$Barangay)[names(share)]
  par(mar = c(5, 13, 4, 2))
  b <- barplot(share, horiz = TRUE, las = 1, xlim = c(0, 108), col = "#D55E00", border = NA, cex.names = 0.75, xaxt = "n",
               names.arg = paste0(names(share), " (", as.vector(n), ")"),
               main = "Households High risk in at least one event", cex.main = 1.05,
               xlab = "Share of households (%). Dashed line: all households")
  axis(1, at = seq(0, 100, 20))
  text(share, b, sprintf("%.0f", share), pos = 4, cex = 0.7)
  abline(v = 100 * mean(hh$AnyHigh), lty = 2)
})

# Save the R session details for reproducibility
writeLines(capture.output(sessionInfo()), file.path(OUT_DIR, "session_info.txt"))
cat("\nDone. Files written to the outputs folder:\n")
print(list.files(OUT_DIR))
