from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="Zoho Finance MIS",
    page_icon="📊",
    layout="wide",
)

PROJECT_FOLDER = Path(__file__).resolve().parent
MAPPING_FILE = PROJECT_FOLDER / "data" / "raw" / "field_mapping.csv"
PIPELINE_SCRIPT = PROJECT_FOLDER / "run_pipeline.py"
EXCEL_SCRIPT = PROJECT_FOLDER / "generate_excel_mis.py"


def read_finance_file(uploaded_file):
    """Read a CSV or Excel upload into a pandas DataFrame."""

    extension = Path(uploaded_file.name).suffix.lower()

    if extension == ".csv":
        dataframe = pd.read_csv(uploaded_file)
    else:
        dataframe = pd.read_excel(uploaded_file)

    dataframe.columns = [
        str(column).strip().lower().replace(" ", "_")
        for column in dataframe.columns
    ]

    return dataframe


st.title("📊 Zoho Finance MIS Automation")

st.write(
    "Upload Zoho Books exports to validate, reconcile and "
    "generate a downloadable management MIS report."
)

st.info(
    "The mapping file and reporting logic are built into the application."
)

reporting_date = st.date_input(
    "MIS reporting date",
    value=pd.Timestamp.today().date(),
)

st.subheader("Upload Zoho exports")

upload_requirements = [
    ("Sales Invoices", "sales_invoices.csv"),
    ("Customer Payments", "customer_payments.csv"),
    ("Vendor Bills", "vendor_bills.csv"),
    ("Vendor Payments", "vendor_payments.csv"),
    ("Expenses", "expenses.csv"),
    ("General Ledger", "general_ledger.csv"),
    ("Budget", "budget.csv"),
]

left_column, right_column = st.columns(2)
page_columns = [left_column, right_column]

uploaded_files = {}

for position, (display_name, required_name) in enumerate(
    upload_requirements
):
    with page_columns[position % 2]:
        uploaded_files[required_name] = st.file_uploader(
            f"{position + 1}. {display_name}",
            type=["csv", "xlsx"],
            key=required_name,
        )


if st.button("Generate MIS Pack", type="primary"):

    missing_files = [
        display_name
        for display_name, required_name in upload_requirements
        if uploaded_files[required_name] is None
    ]

    if missing_files:
        st.error(
            "Please upload: " + ", ".join(missing_files)
        )

    elif not MAPPING_FILE.exists():
        st.error(
            "The built-in field_mapping.csv file could not be found."
        )

    elif not PIPELINE_SCRIPT.exists():
        st.error("run_pipeline.py could not be found.")

    elif not EXCEL_SCRIPT.exists():
        st.error("generate_excel_mis.py could not be found.")

    else:
        try:
            with st.spinner(
                "Validating data and generating the MIS pack..."
            ):
                validation_summary = []

                with tempfile.TemporaryDirectory() as temporary_folder:
                    temporary_path = Path(temporary_folder)

                    input_folder = temporary_path / "input"
                    output_folder = temporary_path / "output"
                    database_path = temporary_path / "finance_mis.db"
                    excel_path = (
                        output_folder / "Automated_Finance_MIS.xlsx"
                    )

                    input_folder.mkdir()
                    output_folder.mkdir()

                    # Convert every upload into the CSV filename
                    # expected by the existing pipeline.
                    for display_name, required_name in upload_requirements:
                        uploaded_file = uploaded_files[required_name]
                        dataframe = read_finance_file(uploaded_file)

                        dataframe.to_csv(
                            input_folder / required_name,
                            index=False,
                        )

                        validation_summary.append(
                            {
                                "Dataset": display_name,
                                "Rows": len(dataframe),
                                "Columns": len(dataframe.columns),
                                "Status": "Loaded",
                            }
                        )

                    # Add the permanent mapping file as the eighth input.
                    shutil.copy2(
                        MAPPING_FILE,
                        input_folder / "field_mapping.csv",
                    )

                    pipeline_command = [
                        sys.executable,
                        str(PIPELINE_SCRIPT),
                        "--input-dir",
                        str(input_folder),
                        "--output-dir",
                        str(output_folder),
                        "--database",
                        str(database_path),
                        "--as-of-date",
                        reporting_date.isoformat(),
                    ]

                    pipeline_result = subprocess.run(
                        pipeline_command,
                        cwd=PROJECT_FOLDER,
                        capture_output=True,
                        text=True,
                        check=True,
                    )

                    excel_command = [
                        sys.executable,
                        str(EXCEL_SCRIPT),
                        "--output",
                        str(excel_path),
                        "--demo-sqlite",
                        str(database_path),
                    ]

                    excel_result = subprocess.run(
                        excel_command,
                        cwd=PROJECT_FOLDER,
                        capture_output=True,
                        text=True,
                        check=True,
                    )

                    if not excel_path.exists():
                        raise FileNotFoundError(
                            "The Excel MIS file was not generated."
                        )

                    # Save file contents before the temporary folder closes.
                    st.session_state["mis_excel"] = (
                        excel_path.read_bytes()
                    )

                    st.session_state["validation_summary"] = (
                        validation_summary
                    )

                    st.session_state["pipeline_log"] = (
                        pipeline_result.stdout
                        + "\n"
                        + excel_result.stdout
                    )

            st.success(
                "MIS validation, reconciliation and Excel generation completed."
            )

        except subprocess.CalledProcessError as error:
            st.error("The MIS pipeline stopped because of an error.")

            with st.expander("View technical error"):
                st.code(error.stdout or "")
                st.code(error.stderr or "")

        except Exception as error:
            st.error(f"MIS generation failed: {error}")


if "validation_summary" in st.session_state:
    summary = pd.DataFrame(
        st.session_state["validation_summary"]
    )

    st.subheader("Processing summary")

    metric_1, metric_2, metric_3 = st.columns(3)

    metric_1.metric("Files processed", len(summary))

    metric_2.metric(
        "Source records",
        f"{summary['Rows'].sum():,}",
    )

    metric_3.metric(
        "Processing status",
        "Completed",
    )

    st.dataframe(
        summary,
        use_container_width=True,
        hide_index=True,
    )


if "mis_excel" in st.session_state:
    st.download_button(
        label="📥 Download Automated Finance MIS",
        data=st.session_state["mis_excel"],
        file_name="Automated_Finance_MIS.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        type="primary",
    )


if "pipeline_log" in st.session_state:
    with st.expander("View pipeline audit log"):
        st.code(st.session_state["pipeline_log"])