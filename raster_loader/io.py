import json

from typing import Iterable

from affine import Affine
import numpy as np
import pandas as pd

import pyproj


def array_to_record(
    arr: np.ndarray,
    geotransform: Affine,
    row_off: int = 0,
    col_off: int = 0,
    value_field: str = "band_1",
    crs: str = "EPSG:4326",
    band: int = 1,
) -> dict:

    height, width = arr.shape

    lon_NW, lat_NW = geotransform * (col_off, row_off)
    lon_NE, lat_NE = geotransform * (col_off + width, row_off)
    lon_SE, lat_SE = geotransform * (col_off + width, row_off + height)
    lon_SW, lat_SW = geotransform * (col_off, row_off + height)

    # required to append dtype to value field name for storage
    dtype_str = str(arr.dtype)
    value_field = "_".join([value_field, dtype_str])

    attrs = {
        "band": band,
        "value_field": value_field,
        "dtype": dtype_str,
        "crs": crs,
        "gdal_transform": geotransform.to_gdal(),
        "row_off": row_off,
        "col_off": col_off,
    }

    record = {
        "lat_NW": lat_NW,
        "lon_NW": lon_NW,
        "lat_NE": lat_NE,
        "lon_NE": lon_NE,
        "lat_SE": lat_SE,
        "lon_SE": lon_SE,
        "lat_SW": lat_SW,
        "lon_SW": lon_SW,
        "block_height": height,
        "block_width": width,
        "attrs": json.dumps(attrs),
        value_field: arr.tobytes(),  # add in endian flag?
    }

    return record


def record_to_array(record: dict, value_field: str = None) -> np.ndarray:
    """Convert a record to a numpy array."""

    if value_field is None:
        value_field = json.loads(record["attrs"])["value_field"]

    # determine dtype
    try:
        dtype_str = value_field.split("_")[-1]
        dtype = np.dtype(dtype_str)
    except TypeError:
        raise TypeError(f"Invalid dtype: {dtype_str}")

    # determine shape
    shape = (record["block_height"], record["block_width"])

    arr = np.frombuffer(record[value_field], dtype=dtype)
    arr = arr.reshape(shape)

    return arr


def import_rasterio():  # pragma: no cover
    try:
        import rasterio

        return rasterio
    except ImportError:

        msg = (
            "Rasterio is not installed.\n"
            "Please install rasterio to use this function.\n"
            "See https://rasterio.readthedocs.io/en/latest/installation.html\n"
            "for installation instructions.\n"
            "Alternatively, run `pip install rasterio` to install from pypi."
        )
        raise ImportError(msg)


def rasterio_windows_to_records(
    file_path: str, band: int = 1, input_crs: str = None
) -> Iterable:
    """Open a raster file with rasterio."""
    rasterio = import_rasterio()

    with rasterio.open(file_path) as raster_dataset:

        raster_crs = raster_dataset.crs.to_string()

        if input_crs is None:
            input_crs = raster_crs
        elif input_crs != raster_crs:
            print(f"WARNING: Input CRS({input_crs}) != raster CRS({raster_crs}).")

        if not input_crs:  # pragma: no cover
            raise ValueError("Unable to find valid input_crs.")

        for _, window in raster_dataset.block_windows():
            rec = array_to_record(
                raster_dataset.read(band, window=window),
                raster_dataset.transform,
                window.row_off,
                window.col_off,
                crs=input_crs,
                band=band,
            )

            if input_crs.upper() != "EPSG:4326":
                rec = reproject_record(rec, input_crs, "EPSG:4326")

            yield rec


def import_bigquery():  # pragma: no cover
    try:
        from google.cloud import bigquery

        return bigquery
    except ImportError:

        msg = (
            "Google Cloud BigQuery is not installed.\n"
            "Please install Google Cloud BigQuery to use this function.\n"
            "See https://googleapis.dev/python/bigquery/latest/index.html\n"
            "for installation instructions.\n"
            "OR, run `pip install google-cloud-bigquery` to install from pypi."
        )
        raise ImportError(msg)


def records_to_bigquery(
    records: Iterable, table_id: str, dataset_id: str, project_id: str, client=None
):
    """Write a record to a BigQuery table."""

    bigquery = import_bigquery()

    if client is None:  # pragma: no cover
        client = bigquery.Client(project=project_id)

    data_df = pd.DataFrame(records)

    client.load_table_from_dataframe(data_df, f"{project_id}.{dataset_id}.{table_id}")


def bigquery_to_records(
    table_id: str, dataset_id: str, project_id: str, limit=10
) -> pd.DataFrame:  # pragma: no cover
    """Read a BigQuery table into a records pandas.DataFrame."""
    bigquery = import_bigquery()

    client = bigquery.Client(project=project_id)

    query = f"SELECT * FROM `{project_id}.{dataset_id}.{table_id}` LIMIT {limit}"

    return client.query(query).result().to_dataframe()


def reproject_record(record: dict, src_crs: str, dst_crs: str = "EPSG:4326") -> dict:
    """Inplace reproject the bounds (lon_NW, lat_NW, etc.) of a record."""

    rasterio = import_rasterio()

    src_crs = rasterio.crs.CRS.from_string(src_crs)
    dst_crs = rasterio.crs.CRS.from_string(dst_crs)

    for lon_col, lat_col in [
        ("lon_NW", "lat_NW"),
        ("lon_NE", "lat_NE"),
        ("lon_SW", "lat_SW"),
        ("lon_SE", "lat_SE"),
    ]:

        transformer = pyproj.Transformer.from_crs(src_crs, dst_crs)
        x, y = transformer.transform(record[lon_col], record[lat_col])
        record[lon_col] = x
        record[lat_col] = y

    return record


def rasterio_to_bigquery(
    file_path: str,
    table_id: str,
    dataset_id: str,
    project_id: str,
    band: int = 1,
    chunk_size: int = None,
    input_crs: int = None,
    client=None,
) -> bool:
    """Write a rasterio-compatible file to a BigQuery table.

    Parameters
    ----------
    file_path : str
        Path to the raster file.
    table_id : str
        BigQuery table name.
    dataset_id : str
        BigQuery dataset name.
    project_id : str
        BigQuery project name.
    band : int, optional
        Band number to read from the raster file, by default 1
    chunk_size : int, optional
        Number of records to write to BigQuery at a time, by default None
    input_crs : int, optional
        Input CRS, by default None
    client : [bigquery.Client()], optional
        BigQuery client, by default None

    Returns
    -------
    bool
        True if successful.
    """

    if isinstance(input_crs, int):
        input_crs = "EPSG:{}".format(input_crs)

    """Write a raster file to a BigQuery table."""
    print("Loading raster file to BigQuery...")

    records_gen = rasterio_windows_to_records(file_path, band, input_crs)

    if chunk_size is None:
        records_to_bigquery(
            records_gen, table_id, dataset_id, project_id, client=client
        )
    else:
        from tqdm.auto import tqdm

        total_blocks = get_number_of_blocks(file_path)

        records = []
        with tqdm(total=total_blocks) as pbar:
            for record in records_gen:
                records.append(record)

                if len(records) >= chunk_size:
                    records_to_bigquery(
                        records, table_id, dataset_id, project_id, client=client
                    )
                    pbar.update(chunk_size)
                    records = []

            if len(records) > 0:
                records_to_bigquery(
                    records, table_id, dataset_id, project_id, client=client
                )
                pbar.update(len(records))

    print("Done.")
    return True


def get_number_of_blocks(file_path: str) -> int:
    """Get the number of blocks in a raster file."""
    rasterio = import_rasterio()

    with rasterio.open(file_path) as raster_dataset:
        return len(list(raster_dataset.block_windows()))


def print_gdalinfo(file_path: str):  # pragma: no cover
    """Print out the output of gdalinfo."""
    import subprocess

    print("Running gdalinfo...")
    subprocess.run(["gdalinfo", file_path])


def size_mb_of_rasterio_band(file_path: str, band: int = 1) -> int:
    """Get the size in MB of a rasterio band."""
    rasterio = import_rasterio()

    with rasterio.open(file_path) as raster_dataset:
        W = raster_dataset.width
        H = raster_dataset.height
        S = np.dtype(raster_dataset.dtypes[band - 1]).itemsize
        return (W * H * S) / 1024 / 1024


def print_band_information(file_path: str):
    """Print out information about the bands in a raster file."""
    rasterio = import_rasterio()

    with rasterio.open(file_path) as raster_dataset:
        print("Number of bands: {}".format(raster_dataset.count))
        print("Band types: {}".format(raster_dataset.dtypes))
        print(
            "Band sizes (MB): {}".format(
                [
                    size_mb_of_rasterio_band(file_path, band + 1)
                    for band in range(raster_dataset.count)
                ]
            )
        )


def get_block_dims(file_path: str) -> tuple:
    """Get the dimensions of a raster file's blocks."""
    rasterio = import_rasterio()

    with rasterio.open(file_path) as raster_dataset:
        return raster_dataset.block_shapes[0]