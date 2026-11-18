CSST 101 - CS 3-B Group 9 - Project AGAP
Statistical Analysis in R (Weeks 1 to 7)

01_Weekly_Forms      The seven weekly progress forms (Week 1 to Week 7), filled in
02_Research_Report   AGAP_CSST101_Research_Report.docx              - the final research report
                     AGAP_CSST101_Results_and_Discussion_Draft.docx - the Week 5 draft of the results and discussion
03_R_Code            AGAP_CSST101_Analysis.R        - the final R script (RStudio)
                     AGAP_CSST101_Analysis_R.ipynb  - the same code as a Colab notebook (R runtime)
                     AGAP_Colab_Data.zip            - the data the code needs
04_R_Outputs         Tables (.csv) and charts (.png) produced by the R script, plus the console output

How to rerun the analysis
  RStudio: keep AGAP_Colab_Data.zip next to the .R file, set the working directory to that folder
           (Session > Set Working Directory > To Source File Location) and click Source.
  Colab:   open the .ipynb, upload AGAP_Colab_Data.zip in the Files panel, then Runtime > Run all.
  Only base R is used, so no packages need to be installed. A run takes 1 to 2 minutes.
  The script prints "24 of 24 values match the independent cross-check" when all is well, and it
  stops with an error if the data do not match the documented counts.

Data use: the dataset contains proprietary barangay boundaries (GEODATA). Share it with the instructor
only; do not post it publicly.

Before submitting: fill in the yellow-highlighted placeholders (names, dates, roles) in the forms and on
the report's title page, and paste screenshots of your own R run into the dashed boxes.
