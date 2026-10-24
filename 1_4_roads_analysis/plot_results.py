#!/usr/bin/env python3
"""Plot road-length correlation matrices and six-city descriptive maps."""

from __future__ import annotations

import os
os.environ.setdefault("MPLCONFIGDIR","/tmp/wb-roads-matplotlib")

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
import numpy as np
import pandas as pd
import seaborn as sns

from common import PIPELINE,load_config,normalize_text

LABELS={"osm":"OSM vehicle roads","overture":"Overture","microsoft":"Microsoft"}
GROUP_LABELS={
 "all_urban_center":"All cities – Urban centers","all_peri_urban":"All cities – Peri-urban",
 "cities_lt_300k_urban_center":"Cities <300k – Urban centers","cities_lt_300k_peri_urban":"Cities <300k – Peri-urban",
 "cities_ge_300k_urban_center":"Cities 300k+ – Urban centers","cities_ge_300k_peri_urban":"Cities 300k+ – Peri-urban"}


def save(fig,path):
    fig.savefig(path.with_suffix(".png"),dpi=300,bbox_inches="tight"); fig.savefig(path.with_suffix(".svg"),bbox_inches="tight"); plt.close(fig)


def plot_correlations(root):
    data=pd.read_csv(root/"figure_5_1_segment_road_length_correlations.csv"); order=list(LABELS)
    fig,axes=plt.subplots(2,3,figsize=(12.5,7.8))
    for ax,(group,title) in zip(axes.flat,GROUP_LABELS.items()):
        d=data.loc[data.reporting_group==group].pivot(index="source_1",columns="source_2",values="correlation").reindex(index=order,columns=order)
        d.index=[LABELS[x] for x in d.index]; d.columns=[LABELS[x] for x in d.columns]
        sns.heatmap(d,ax=ax,vmin=-1,vmax=1,cmap="vlag",center=0,annot=True,fmt=".2f",square=True,cbar=False,linewidths=.5,linecolor="white")
        ax.set_title(title,fontsize=10,weight="bold"); ax.set_xlabel(""); ax.set_ylabel(""); ax.tick_params(axis="x",rotation=30,labelsize=8); ax.tick_params(axis="y",rotation=0,labelsize=8)
    scalar=plt.cm.ScalarMappable(cmap="vlag",norm=plt.Normalize(-1,1)); scalar.set_array([])
    cax=fig.add_axes([.925,.18,.014,.64]); fig.colorbar(scalar,cax=cax,label="Spearman correlation")
    fig.suptitle("Figure 5.1. Segment-level correlation of road-length estimates",weight="bold",y=.985)
    fig.subplots_adjust(left=.08,right=.89,bottom=.10,top=.91,wspace=.43,hspace=.45)
    save(fig,root/"figure_5_1_segment_road_length_correlations")


def example_files(config):
    paths=list((PIPELINE/"outputs"/"segment_roads").glob("*_segment_roads.parquet")); by_name={pd.read_parquet(p,columns=["city_name"]).iloc[0].city_name:p for p in paths}
    normalized={normalize_text(k):v for k,v in by_name.items()}
    for city in config["example_cities"]: yield city,normalized[normalize_text(city)]


def plot_maps(config,root):
    examples=[(city,gpd.read_parquet(path)) for city,path in example_files(config)]; sources=list(LABELS)
    positive=np.concatenate([
        g[f"{source}_total_m"].to_numpy()[g[f"{source}_total_m"].to_numpy()>0]
        for _,g in examples for source in sources
    ])
    norm=LogNorm(vmin=max(1,float(np.nanpercentile(positive,2))),vmax=max(2,float(np.nanpercentile(positive,99))))
    fig,axes=plt.subplots(6,3,figsize=(10.5,18))
    for row,(city,segments) in enumerate(examples):
        for col,source in enumerate(sources):
            ax=axes[row,col]; segments.plot(column=f"{source}_total_m",ax=ax,cmap="viridis",norm=norm,linewidth=0,missing_kwds={"color":"#f0f0f0"})
            ax.set_axis_off(); ax.set_title(f"{city} – {LABELS[source]}",fontsize=9,weight="bold",pad=3)
    scalar=plt.cm.ScalarMappable(cmap="viridis",norm=norm); scalar.set_array([])
    cax=fig.add_axes([.22,.018,.56,.012]); fig.colorbar(scalar,cax=cax,orientation="horizontal",label="Road length per segment (m)")
    fig.suptitle("Road length per neighborhood segment in six example cities",weight="bold",y=.992)
    fig.subplots_adjust(left=.015,right=.985,bottom=.055,top=.955,wspace=.04,hspace=.13)
    save(fig,root/"figure_5_2_example_city_segment_road_lengths")


def main():
    config=load_config(); root=PIPELINE/"outputs"/"analysis"; root.mkdir(parents=True,exist_ok=True); plot_correlations(root); plot_maps(config,root); print(f"Wrote plots to {root}")


if __name__=="__main__": main()
