from dash import Dash, dcc, html
import dash_daq as daq
from dash.dependencies import Input, Output
import dash_bootstrap_components as dbc
from dash_bootstrap_templates import ThemeChangerAIO, template_from_url

import pandas as pd
import numpy as np
from os import walk
from dateutil import parser
from datetime import datetime

dbc_css = "https://cdn.jsdelivr.net/gh/AnnMarieW/dash-bootstrap-templates/dbc.min.css"

app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP, dbc_css])

df = pd.read_csv('df.csv')
df_solar = pd.read_csv('df_solar.csv')

def irradiance_to_pv_production(solar, conversion_value_irradiance, solar_panel_area, efficiency):

    # calculate irrediance kwh per m2
    solar['irradiance (kwh/m2)'] = solar['radiation'] * conversion_value_irradiance
    
    # calculate total kwh
    solar['electricity production (kwh)'] = solar_panel_area * solar['irradiance (kwh/m2)'] * efficiency
    
    return solar

def data_prep(df_wu, df_solar, conversion_value_irradiance, solar_panel_area, efficiency, start_date, end_date):
    
    # data voorbereiden
    df_wu.rename(columns = {'price':'net price'}, inplace = True)
    
    # voeg solar data toe
    df_solar = irradiance_to_pv_production(df_solar, conversion_value_irradiance, solar_panel_area, efficiency)
    df_wu = df_wu.merge(df_solar, on = 'time', how =  'inner')
    df_wu = df_wu.drop(['radiation', 'irradiance (kwh/m2)'], axis = 1)

    print(df_wu['time'].dtype)
    print(type(start_date))

    # selecteer datums
    mask = ( pd.to_datetime(df_wu['time']) > start_date) & (pd.to_datetime(df_wu['time']) <= end_date)
    df_wu = df_wu.loc[mask]
    
    # voeg lege kolommen voor nieuwe variabelen toe
    for col in ['battery charge (kwh)', 'solar charge (kwh)', 'net charge (kwh)', 'net decharge (kwh)',  'net costs (€)', 'net revenue (€)']:
        df_wu[col] = 0.0
        
    return df_wu

def charge_calc(capacity, following_hours, charging_speed):
    
    if capacity - max(following_hours['battery charge (kwh)'].tolist()) <= charging_speed:
        charge = capacity - max(following_hours['battery charge (kwh)'].tolist()) 
    else:
        charge = charging_speed
        
    return charge

def charge(df_date, solar, capacity, charging_speed_b, charging_speed_h, max_price):
    
    # sorteer op goedkoopste uren eerst
    df_date = df_date.sort_values(['net price'])
    indexes = list(df_date.index.values)

    # loop over alle uren (goedkoopste eerst)
    for index, row in df_date.iterrows():

        # verkrijg de uren die volgen na het huidige uur
        following_hours = df_date.sort_values(['time']).loc[index:max(indexes)]
        
        # charging capacity = capacity - usage
        capacity = capacity - df_date.at[index, 'total usage (kwh)']
        
        # als er nog bij geladen kan worden die dag
        if max(following_hours['battery charge (kwh)'].tolist()) < capacity:
        
            # als het net negatieve prijzen heeft of gratis is
            if row['net price'] <= 0:
                
                # --> zonnepanelen uitschakelen
                
                # hoeveel kun je dan bijladen?
                charge = charge_calc(capacity, following_hours, min([charging_speed_b, charging_speed_h]))
                
                # opladen
                df_date.at[index, 'net charge (kwh)'] = charge
                for i in following_hours.index:
                    df_date.at[i, 'battery charge (kwh)'] += charge
                    
                # "betalen" (negatieve kosten)
                df_date.at[index, 'net costs (€)'] = df_date.at[index, 'net price'] * df_date.at[index, 'net charge (kwh)']
            
            # als het net NIET goedkoper is maar er is wel zon
            elif row['electricity production (kwh)'] > 0:
                
                # hoeveel kun je dan bijladen?
                charge = charge_calc(capacity, following_hours, min([charging_speed_b, charging_speed_h]))
                
                solar_charge = 0
                net_charge = 0
                
                if solar == True:
                    # evenveel zon als batterij capaciteit
                    if row['electricity production (kwh)'] == charge:
                        solar_charge = row['electricity production (kwh)']

                    # minder zon dan batterij capaciteit
                    elif row['electricity production (kwh)'] < charge:
                        solar_charge = row['electricity production (kwh)']

                        # prijs is laag? bijkopen
                        if row['net price'] <= max_price:
                            net_charge = charge - solar_charge

                    # meer zon dan batterij capaciteit
                    elif row['electricity production (kwh)'] > charge:

                        solar_charge = charge

                        # prijs is positief? terugleveren
                        if row['net price'] > 0:
                            df_date.at[index, 'net revenue (€)'] = row['net price'] * min([charging_speed_h, (row['electricity production (kwh)'] - solar_charge)])
                
                else:
                    # prijs is laag? bijkopen
                    if row['net price'] <= max_price:
                        net_charge = charge    
                
                # opladen
                df_date.at[index, 'net charge (kwh)'] = net_charge
                df_date.at[index, 'solar charge (kwh)'] = solar_charge
                for i in following_hours.index:
                    df_date.at[i, 'battery charge (kwh)'] += solar_charge + net_charge
                    
                # betalen
                if net_charge > 0:
                    df_date.at[index, 'net costs (€)'] = df_date.at[index, 'net price'] * df_date.at[index, 'net charge (kwh)']
            
            # als het net NIET goedkoper is en er is geen zon, maar het net is wel goedkoop
            elif row['net price'] <= max_price:
                
                # hoeveel kun je dan bijladen?
                charge = charge_calc(capacity, following_hours, min([charging_speed_b, charging_speed_h]))
                
                # opladen
                df_date.at[index, 'net charge (kwh)'] = charge
                for i in following_hours.index:
                    df_date.at[i, 'battery charge (kwh)'] += charge
                
                # betalen
                df_date.at[index, 'net costs (€)'] = df_date.at[index, 'net price'] * df_date.at[index, 'net charge (kwh)']
            
        else:
            # is de batterij vol, maar er is wel zon en zonnepanelen, en de prijzen zijn positief? terugleveren!
            if row['electricity production (kwh)'] > 0 and row['net price'] > 0 and solar == True:
                df_date.at[index, 'net revenue (€)'] = row['electricity production (kwh)'] * row['net price']
                  
    return df_date

def decharge(df_date, capacity, charging_speed_b, charging_speed_h):
    
    # sorteer op de duurste uren eerst
    df_date = df_date.sort_values(['net price'], ascending = False)
    indexes = list(df_date.index.values)
    
    # minste snelheid wordt snelheid
    charging_speed = min([charging_speed_b, charging_speed_h])
    
    # loop over alle uren (duurste eerst)
    for index, row in df_date.iterrows():
        
        # verkrijg de uren die volgen na het huidige uur
        following_hours = df_date.sort_values(['time']).loc[index:max(indexes)]
        
        # is er nog eneragie over?
        if min(following_hours['battery charge (kwh)'].tolist()) > 0.0:
        
            # meer energie over dan laadsnelheid
            if min(following_hours['battery charge (kwh)'].tolist()) > charging_speed:
                charge = charging_speed
            
            # minder energie over dan laadsnelheid
            else:
                charge = min(following_hours['battery charge (kwh)'].tolist())
                
            # ontladen
            df_date.at[index, 'net decharge (kwh)'] = charge
            for i in following_hours.index:
                df_date.at[i, 'battery charge (kwh)'] -= charge
                    
            # terugleveren
            df_date.at[index, 'net revenue (€)'] = df_date.at[index, 'net price'] * charge
            
    return df_date

def finance(solar, df_wu, capacity, charging_speed_b, num_days, solar_panel_kwh, 
            battery_cost_vast, battery_cost_var_cap, battery_cost_var_char, solar_panel_cost_per_kwh):
    
    # kosten
    battery_cost = battery_cost_vast + (capacity * battery_cost_var_cap) + (charging_speed_b * battery_cost_var_char)
    
    if solar == True:
        solar_panel_cost = solar_panel_kwh * solar_panel_cost_per_kwh
    else:
        solar_panel_cost = 0
        
    total_costs = battery_cost + solar_panel_cost
      
    # opbrengsten
    revenue = df_wu.sum()['net revenue (€)'] - df_wu.sum()['net costs (€)'] - df_wu.sum()['only net usage (price)']
    avg_revenue = revenue / num_days.days
    
    # terugverdientijd
    if avg_revenue <= 0.0:
        payback = 'nooit'
    else:
        payback = str(round(total_costs / avg_revenue / 365, 1))
    
    return revenue, avg_revenue, payback

def calc_profit(df, df_solar, solar, start_date, end_date, capacity, charging_speed_b, charging_speed_h, spread,
               solar_panel_area, solar_panel_kwh, conversion_value_irradiance, efficiency, battery_cost_vast, 
               battery_cost_var_cap, battery_cost_var_char, solar_panel_cost_per_kwh):
    
    # kopieer dataset (zodat originele niet verloren gaat)
    df_wu = df.copy()
    
    # vertaal min en max datum
    start_date = df_wu.iloc[0]['time'] if start_date == 'min' else datetime.strptime(start_date, "%Y-%m-%d")
    end_date = df_wu.iloc[-1]['time'] if end_date == 'max' else datetime.strptime(end_date, "%Y-%m-%d")
    
    # data voorbereiden
    df_wu = data_prep(df_wu, df_solar, conversion_value_irradiance, solar_panel_area, efficiency, start_date, end_date)
    
    # loop over alle dagen
    for date in range(0, len(df_wu['date'].unique())):

        # selecteer datum
        df_date = df_wu.loc[df['date'] == df_wu['date'].unique()[date]]

        # haal de resterende energie van gisteren op en zet die als vandaag
        yesterday = df_date.index[0] - 1 if df_date.index[0] - 1 > df_wu.index[0] else df_wu.index[0]
        df_date['battery charge (kwh)'] = df_wu.at[yesterday, 'battery charge (kwh)']
        
        # spreiding naar maximale prijs
        max_price = df_date['net price'].max() - spread

        # charge
        df_date = charge(df_date, solar, capacity, charging_speed_b, charging_speed_h, max_price)

        # decharge
        df_date = decharge(df_date, capacity, charging_speed_b, charging_speed_h)

        # update de dag in het dataframe
        df_wu.update(df_date)

        num_days = datetime.strptime(str(end_date), "%Y-%m-%d %H:%M:%S") - datetime.strptime(str(start_date), "%Y-%m-%d %H:%M:%S")

    revenue, avg_revenue, payback = finance(solar, df_wu, capacity, charging_speed_b, num_days, solar_panel_kwh, battery_cost_vast, battery_cost_var_cap, battery_cost_var_char, solar_panel_cost_per_kwh)
    
    return df_wu, revenue, avg_revenue, payback

count = 0

app.layout = html.Div([
    html.Div(
        [
            html.H3("Algemeen"),
            html.Br(),
            html.P("Start datum"),
            html.P("yyyy-mm-dd, min of max", style={'font-style': 'italic'}),
            dcc.Input(
                id="start_date",
                type="text",
                value="2022-01-01",
            ),
            html.Br(), html.Br(),
            html.P("Eind datum"),
            html.P("yyyy-mm-dd, min of max", style={'font-style': 'italic'}),
            dcc.Input(
                id="end_date",
                type="text",
                value="max",
            ),
            html.Br(), html.Br(),
            html.P("Spreiding"),
            dcc.Input(
                id="spread",
                type="number",
                value=0.05,
            )
        ], style={
            'display': 'inline-block', 
            'margin': 50,
            'vertical-align': 'top'
            }
    ),
    html.Div(
        [
            html.H3("Batterij"),
            html.Br(),
            html.P("Capaciteit"),
            dcc.Input(
                id="capacity",
                type="number",
                value=5.0,
            ),
            html.Br(), html.Br(),
            html.P("Vermogen batterij"),
            dcc.Input(
                id="charging_speed_b",
                type="number",
                value=3.7,
            ),
            html.Br(), html.Br(),
            html.P("Vermogen huis"),
            dcc.Input(
                id="charging_speed_h",
                type="number",
                value=11.0,
            ),
            html.Br(), html.Br(),
            html.P("Vaste kosten"),
            dcc.Input(
                id="battery_cost_vast",
                type="number",
                value=1350,
            ),
            html.Br(), html.Br(),
            html.P("Variabele kosten (capaciteit)"),
            dcc.Input(
                id="battery_cost_var_cap",
                type="number",
                value=450,
            ),
            html.Br(), html.Br(),
            html.P("Variabele kosten (vermogen)"),
            dcc.Input(
                id="battery_cost_var_char",
                type="number",
                value=350,
            ),
        ], style={
            'display': 'inline-block', 
            'margin': 50,
            'vertical-align': 'top'
            }
    ),
    html.Div(
        [
            html.H3("Zonnepanelen"),
            html.Br(),
            daq.BooleanSwitch(id="solar", on=True, color="#6b8ea6"),
            html.Br(), 
            html.P("Oppervlakte zonnepaneel"),
            dcc.Input(
                id="solar_panel_size",
                type="number",
                value=1.5,
            ),
            html.Br(), html.Br(),
            html.P("Aantal zonnepanelen"),
            dcc.Input(
                id="solar_panel_num",
                type="number",
                value=8,
            ),
            html.Br(), html.Br(),
            html.P("Kosten per kwh"),
            dcc.Input(
                id="solar_panel_cost_per_kwh",
                type="number",
                value=1.51,
            ),
            html.Br(), html.Br(), 
            html.P("Conversie irrediance"),
            dcc.Input(
                id="conversion_value_irradiance",
                type="number",
                value=0.002778,
            ),
            html.Br(), html.Br(), 
            html.P("Efficiëntie"),
            dcc.Input(
                id="efficiency",
                type="number",
                value=0.20,
            ),
        ], style={
            'display': 'inline-block', 
            'margin': 50,
            'vertical-align': 'top'
            }

    ),
    html.Div(
        [
            html.H3("Output"),
            html.Br(), 
            html.Button('Bereken', id='submit', n_clicks=0),
            html.Br(), html.Br(), 
            html.P("Totale opbrengst"),
            html.P(id='output1'),
            html.Br(), 
            html.P("Gemiddelde dagelijkse opbrengst"),
            html.P(id='output2'),
            html.Br(), 
            html.P("Terugverdientijd in jaren"),
            html.P(id='output3'),

        ], style={
            'display': 'inline-block', 
            'margin': 50,
            'margin-left': 100,
            'padding-left': 100,
            'border-left': '1px solid black',
            'vertical-align': 'top'
            }
    )
])


@app.callback(
    [
        Output('output1', 'children'), 
        Output('output2', 'children'),
        Output('output3', 'children'),
    ],
    [
        Input("solar", "on"),
        Input("start_date", "value"),
        Input("end_date", "value"),
        Input("capacity", "value"),
        Input("charging_speed_b", "value"),
        Input("charging_speed_h", "value"),
        Input("spread", "value"),
        Input("conversion_value_irradiance", "value"),
        Input("efficiency", "value"),
        Input("battery_cost_vast", "value"),
        Input("solar_panel_size", "value"),
        Input("solar_panel_num", "value"),
        Input("battery_cost_var_cap", "value"),
        Input("battery_cost_var_char", "value"),
        Input("solar_panel_cost_per_kwh", "value"),
        Input("submit", "n_clicks")
    ],
)
def output_text(solar, start_date, end_date, capacity, charging_speed_b, charging_speed_h, spread,
               conversion_value_irradiance, efficiency, battery_cost_vast, solar_panel_size, solar_panel_num,
               battery_cost_var_cap, battery_cost_var_char, solar_panel_cost_per_kwh, submit):

    global count

    solar = "{}".format(solar)

    if solar == 'True':
        solar = True
    else:
        solar = False

    if count < submit:

        count += 1

        solar_panel_area = solar_panel_size * solar_panel_num
        solar_panel_kwh = solar_panel_num * 250

        result = calc_profit(df, df_solar, solar, start_date, end_date, capacity, charging_speed_b, charging_speed_h, spread,
               solar_panel_area, solar_panel_kwh, conversion_value_irradiance, efficiency, battery_cost_vast, 
               battery_cost_var_cap, battery_cost_var_char, solar_panel_cost_per_kwh)

        return '€ ' + str(round(result[1], 2)), '€ ' + str(round(result[2], 2)), result[3]

    return '-', '-', '-'

if __name__ == "__main__":
    app.run_server(debug=True)