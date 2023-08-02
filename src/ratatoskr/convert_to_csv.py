#!/usr/bin/python
# Reference: https://betterprogramming.pub/using-python-to-convert-worksheets-in-an-excel-file-to-separate-csv-files-7dd406b652d7
# This python script is to extract each sheet in an Excel workbook as a new csv file

import csv
import sys

import xlrd


def ExceltoCSV(excel_file, csv_file):
    """Convert Excel XLS file to CSV"""

    # Create Excel workbook object
    workbook = xlrd.open_workbook(excel_file)

    # Iterate over each tab/sheet name
    for sheet_name in workbook.sheet_names():
        print(f"[-] INFO Processing sheet name - {sheet_name}")

        # Define our worksheet by index
        sh = workbook.sheet_by_index(0)

        with open(csv_file, "w") as csv_fh:
            writetocsv = csv.writer(csv_fh)
            for rownum in range(sh.nrows):
                new_row = []
                for cell in sh.row(rownum):
                    new_row.append(cell.value)
                writetocsv.writerow(new_row)

        print(f"{sheet_name} has been saved at - {csv_file}")


if __name__ == "__main__":
    ExceltoCSV(excel_file=sys.argv[1], csv_file=sys.argv[2])
