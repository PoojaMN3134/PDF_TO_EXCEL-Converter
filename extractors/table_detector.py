def detect_tables(page):

    try:
        tables = page.find_tables()

        return tables.tables

    except:

        return []