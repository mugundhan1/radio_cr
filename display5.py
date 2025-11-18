import dash
from dash import dcc, html, Input, Output, State, callback_context
import plotly.graph_objs as go
from plotly.subplots import make_subplots
import pandas as pd
import threading
import queue
import time
from datetime import datetime, timedelta
import os
import glob
from collections import deque

# Configuration
CSV_FOLDER = "./tinysa_data"  # Match your output_dir from tinySA script
UPDATE_INTERVAL = 1000  # milliseconds (1 second)
MAX_QUEUE_SIZE = 864002
TIME_WINDOW_MINUTES = 15  # Show last 15 minutes

# Thread-safe queue for data
data_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)

# Global variables
current_file = None
current_sweep_index = 0
last_row_count = 0
is_running = True
is_paused = False

# Power integration history with timestamps (for 15-minute window)
power_history = deque()  # Stores (timestamp, power) tuples
time_history = deque()


class CSVReader(threading.Thread):
    """Background thread to read CSV files and push data to queue"""
    
    def __init__(self, data_queue):
        threading.Thread.__init__(self)
        self.data_queue = data_queue
        self.daemon = True
        
    def get_latest_csv_file(self):
        """Get the most recent CSV file from the folder"""
        csv_files = glob.glob(os.path.join(CSV_FOLDER, "*.csv"))
        if not csv_files:
            return None
        latest_file = max(csv_files, key=os.path.getctime)
        return latest_file
    
    def run(self):
        global current_file, current_sweep_index, last_row_count, is_paused
        
        while is_running:
            try:
                # Skip if paused
                if is_paused:
                    time.sleep(0.5)
                    continue
                
                # Get the latest CSV file
                latest_file = self.get_latest_csv_file()
                
                if latest_file is None:
                    time.sleep(1)
                    continue
                
                # Check if file has changed (new day/new file)
                if latest_file != current_file:
                    print(f"Switched to new file: {latest_file}")
                    current_file = latest_file
                    current_sweep_index = 0
                    last_row_count = 0
                
                # Read CSV file
                df = pd.read_csv(current_file)
                
                # Debug: Print column names on first read
                if last_row_count == 0 and len(df) > 0:
                    freq_cols = [col for col in df.columns if col != 'Timestamp']
                    try:
                        low_mhz = float(freq_cols[0]) / 1e6
                        high_mhz = float(freq_cols[-1]) / 1e6
                        print(f"CSV loaded: {len(freq_cols)} frequency points")
                        print(f"Frequency range: {low_mhz:.3f} MHz to {high_mhz:.3f} MHz")
                    except Exception:
                        print(f"CSV loaded: {len(freq_cols)} frequency points (frequency names not numeric)")
                
                # Check if new rows have been added
                current_row_count = len(df)
                if current_row_count <= last_row_count:
                    time.sleep(0.5)  # Check more frequently for new data
                    continue
                
                # Process new rows (could be multiple if we missed some)
                for idx in range(last_row_count, current_row_count):
                    if idx >= len(df):
                        break
                        
                    row_data = df.iloc[idx]
                    timestamp = row_data['Timestamp']
                    
                    # Extract frequency columns (all columns except Timestamp)
                    freq_columns = [col for col in df.columns if col != 'Timestamp']
                    
                    # Convert column names (Hz) to MHz - these are the actual frequencies
                    try:
                        frequencies = [float(col) / 1e6 for col in freq_columns]
                    except Exception:
                        # If column names are already in MHz or not numeric, keep as-is (attempt to cast gracefully)
                        frequencies = []
                        for col in freq_columns:
                            try:
                                frequencies.append(float(col) / 1e6)
                            except Exception:
                                # fallback: use index-based evenly spaced values
                                frequencies = list(range(len(freq_columns)))
                                break
                    
                    # Extract power values from this specific row
                    power_values = [float(row_data[col]) for col in freq_columns]
                    
                    # CORRECT METHOD: Calculate integrated power (sum in linear scale)
                    # Convert dBm to linear power (mW), sum, then convert back to dBm
                    try:
                        linear_powers = [10**(p/10) for p in power_values]  # Convert to mW
                        total_linear_power = sum(linear_powers)  # Sum in linear scale
                        integrated_power_dbm = 10 * math.log10(total_linear_power) if total_linear_power > 0 else min(power_values)
                    except:
                        # Fallback to simple average if calculation fails
                        integrated_power_dbm = sum(power_values) / len(power_values)
                    
                    # Parse timestamp to datetime object
                    try:
                        dt = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S.%f')
                    except:
                        # Fallback: use current time
                        dt = datetime.now()
                    
                    # Verify data alignment (only on first row for debugging)
                    if idx == 0:
                        peak_power = max(power_values)
                        peak_idx = power_values.index(peak_power)
                        if len(frequencies) > peak_idx:
                            peak_freq = frequencies[peak_idx]
                            try:
                                print(f"First sweep - Peak: {peak_freq:.3f} MHz at {peak_power:.2f} dBm")
                                print(f"Integrated power (correct method): {integrated_power_dbm:.2f} dBm")
                            except:
                                print(f"First sweep - Peak index {peak_idx} at {peak_power:.2f} dBm")
                        else:
                            print(f"First sweep - Peak index {peak_idx} at {peak_power:.2f} dBm (frequency names unavailable)")
                    
                    # Prepare data packet
                    data_packet = {
                        'timestamp': timestamp,
                        'datetime': dt,
                        'frequencies': frequencies,
                        'power_values': power_values,
                        'sweep_index': idx,
                        'total_sweeps': current_row_count,
                        'filename': os.path.basename(current_file),
                        'integrated_power': integrated_power_dbm
                    }
                    
                    # Put data in queue (non-blocking)
                    if not self.data_queue.full():
                        self.data_queue.put(data_packet)
                
                # Update last row count
                last_row_count = current_row_count
                
                time.sleep(0.5)  # Check for new data every 500ms
                
            except Exception as e:
                print(f"Error reading CSV: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(1)


# Initialize Dash app
app = dash.Dash(__name__, update_title=None)
app.title = "Real-time Spectrum Analyzer - tinySA"

# Layout
app.layout = html.Div([
    html.Div([
        html.H1("Real-time Spectrum Analyzer", 
                style={'color': '#06b6d4', 'textAlign': 'center', 'marginBottom': '8px'}),
        
        # Info bar
        html.Div([
            html.Div([
                html.Span("📅 Date: ", style={'color': '#9ca3af'}),
                html.Span(id='current-date', style={'fontFamily': 'monospace'})
            ], style={'display': 'inline-block', 'marginRight': '30px'}),
            
            html.Div([
                html.Span("🕐 Time: ", style={'color': '#9ca3af'}),
                html.Span(id='current-time', style={'fontFamily': 'monospace'})
            ], style={'display': 'inline-block', 'marginRight': '30px'}),
            
            html.Div([
                html.Span("📊 Sweep: ", style={'color': '#9ca3af'}),
                html.Span(id='sweep-info', style={'fontFamily': 'monospace'})
            ], style={'display': 'inline-block', 'marginRight': '30px'}),
            
            html.Div([
                html.Span("📍 Points: ", style={'color': '#9ca3af'}),
                html.Span(id='data-points', style={'fontFamily': 'monospace'})
            ], style={'display': 'inline-block', 'marginRight': '30px'}),
            
            html.Div([
                html.Span("⚡ Total Power: ", style={'color': '#9ca3af'}),
                html.Span(id='total-power', style={'fontFamily': 'monospace', 'color': '#22c55e', 'fontWeight': 'bold'})
            ], style={'display': 'inline-block', 'marginRight': '30px'}),
            
            html.Div([
                html.Span("📁 File: ", style={'color': '#9ca3af'}),
                html.Span(id='current-file', style={'fontFamily': 'monospace', 'fontSize': '12px'})
            ], style={'display': 'inline-block', 'marginRight': '30px'}),
            
            # Controls in the info bar
            html.Div([
                html.Button(
                    '⏸',
                    id='play-button',
                    n_clicks=0,
                    style={
                        'padding': '6px 14px',
                        'fontSize': '14px',
                        'fontWeight': 'bold',
                        'backgroundColor': '#ef4444',
                        'color': 'white',
                        'border': 'none',
                        'borderRadius': '6px',
                        'cursor': 'pointer',
                        'marginRight': '8px',
                        'minWidth': '40px'
                    }
                ),
                html.Button(
                    '🔄',
                    id='reset-button',
                    n_clicks=0,
                    style={
                        'padding': '6px 14px',
                        'fontSize': '14px',
                        'fontWeight': 'bold',
                        'backgroundColor': '#3b82f6',
                        'color': 'white',
                        'border': 'none',
                        'borderRadius': '6px',
                        'cursor': 'pointer',
                        'marginRight': '15px',
                        'minWidth': '40px'
                    }
                ),
                html.Div(id='status-indicator', style={
                    'width': '8px',
                    'height': '8px',
                    'borderRadius': '50%',
                    'backgroundColor': '#22c55e',
                    'display': 'inline-block',
                    'marginRight': '6px',
                    'verticalAlign': 'middle'
                }),
                html.Span(id='status-text', children='Live', 
                         style={'fontFamily': 'monospace', 'fontSize': '13px', 'color': '#d1d5db', 'verticalAlign': 'middle'})
            ], style={'display': 'inline-block'}),
        ], style={'textAlign': 'center', 'color': '#d1d5db', 'fontSize': '14px', 'marginBottom': '8px'}),
    ], style={'padding': '15px 20px 5px 20px', 'backgroundColor': '#0f172a'}),
    
    # Side-by-side graphs container
    html.Div([
        # Spectrum Graph (Left)
        html.Div([
            html.Div([
                html.Span(id='graph-title', children='Spectrum', 
                         style={'fontSize': '18px', 'fontWeight': 'bold', 'color': '#06b6d4'})
            ], style={'padding': '12px 20px', 'backgroundColor': '#1e293b', 'borderRadius': '10px 10px 0 0', 
                     'borderBottom': '1px solid #334155', 'textAlign': 'center'}),
            dcc.Graph(
                id='spectrum-graph',
                config={'displayModeBar': True, 'displaylogo': False},
                style={'height': '550px', 'marginTop': '0'}
            )
        ], style={'flex': '1', 'padding': '0', 'backgroundColor': '#1e293b', 
                  'borderRadius': '10px', 'boxShadow': '0 4px 6px rgba(0, 0, 0, 0.3)', 'marginRight': '10px'}),
        
        # Power Integration Graph (Right)
        html.Div([
            html.Div([
                html.Span(f'⚡ Integrated Power - Last {TIME_WINDOW_MINUTES} Minutes', 
                         style={'fontSize': '18px', 'fontWeight': 'bold', 'color': '#22c55e'})
            ], style={'padding': '12px 20px', 'backgroundColor': '#1e293b', 'borderRadius': '10px 10px 0 0', 
                     'borderBottom': '1px solid #334155', 'textAlign': 'center'}),
            dcc.Graph(
                id='power-integration-graph',
                config={'displayModeBar': True, 'displaylogo': False},
                style={'height': '550px', 'marginTop': '0'}
            )
        ], style={'flex': '1', 'padding': '0', 'backgroundColor': '#1e293b', 
                  'borderRadius': '10px', 'boxShadow': '0 4px 6px rgba(0, 0, 0, 0.3)', 'marginLeft': '10px'}),
    ], style={'display': 'flex', 'margin': '0 20px', 'gap': '0px'}),
    
    # Footer
    html.Div([
        html.P(f"Data Directory: {CSV_FOLDER} | Time Window: {TIME_WINDOW_MINUTES} minutes | Power calculated using linear scale summation", 
               style={'textAlign': 'center', 'color': '#6b7280', 'fontSize': '12px', 'marginTop': '20px'})
    ]),
    
    # Hidden div to store state
    dcc.Store(id='is-playing', data=True),
    
    # Interval component for updates
    dcc.Interval(
        id='interval-component',
        interval=UPDATE_INTERVAL,
        n_intervals=0
    )
], style={'backgroundColor': '#0f172a', 'minHeight': '100vh', 'color': 'white', 'fontFamily': 'Arial, sans-serif'})


# Callbacks
@app.callback(
    [Output('spectrum-graph', 'figure'),
     Output('power-integration-graph', 'figure'),
     Output('current-date', 'children'),
     Output('current-time', 'children'),
     Output('sweep-info', 'children'),
     Output('data-points', 'children'),
     Output('current-file', 'children'),
     Output('graph-title', 'children'),
     Output('total-power', 'children')],
    [Input('interval-component', 'n_intervals')],
    [State('is-playing', 'data')]
)
def update_graph(n, is_playing):
    if not is_playing:
        return [dash.no_update] * 9
    
    try:
        # Get data from queue (non-blocking)
        data_packet = data_queue.get_nowait()
        
        frequencies = data_packet['frequencies']
        power_values = data_packet['power_values']
        timestamp = data_packet['timestamp']
        dt = data_packet['datetime']
        sweep_index = data_packet['sweep_index']
        total_sweeps = data_packet['total_sweeps']
        filename = data_packet['filename']
        integrated_power = data_packet['integrated_power']
        
        # Add to power history with timestamp
        power_history.append((dt, integrated_power))
        
        # Remove data older than TIME_WINDOW_MINUTES
        cutoff_time = datetime.now() - timedelta(minutes=TIME_WINDOW_MINUTES)
        while power_history and power_history[0][0] < cutoff_time:
            power_history.popleft()
        
        # Verify data alignment
        if len(frequencies) != len(power_values):
            print(f"WARNING: Frequency/Power mismatch! {len(frequencies)} vs {len(power_values)}")
            return [dash.no_update] * 9
        
        # Calculate statistics
        peak_power = max(power_values)
        peak_idx = power_values.index(peak_power)
        peak_frequency = frequencies[peak_idx] if len(frequencies) > peak_idx else peak_idx
        avg_power = sum(power_values) / len(power_values)
        
        # Debug output for first few updates
        if sweep_index < 3:
            try:
                print(f"Sweep {sweep_index}: Peak at {peak_frequency:.3f} MHz ({peak_power:.2f} dBm), Integrated Power: {integrated_power:.2f} dBm")
            except:
                print(f"Sweep {sweep_index}: Peak idx {peak_idx} ({peak_power:.2f} dBm), Integrated Power: {integrated_power:.2f} dBm")
        
        # Create spectrum figure
        spectrum_fig = go.Figure()
        
        # Add spectrum trace with filled area
        spectrum_fig.add_trace(go.Scatter(
            x=frequencies,
            y=power_values,
            mode='lines',
            name='Spectrum',
            line=dict(color='#06b6d4', width=2),
            fill='tozeroy',
            fillcolor='rgba(6, 182, 212, 0.3)',
            hovertemplate='<b>Frequency:</b> %{x:.3f} MHz<br><b>Power:</b> %{y:.2f} dBm<extra></extra>'
        ))
        
        # Add average line
        spectrum_fig.add_hline(
            y=avg_power,
            line_dash="dash",
            line_color="#22c55e",
            line_width=2,
            annotation_text=f"Avg: {avg_power:.2f} dBm",
            annotation_position="right",
            annotation_font_color="#22c55e"
        )
        
        # Add peak marker
        spectrum_fig.add_trace(go.Scatter(
            x=[peak_frequency],
            y=[peak_power],
            mode='markers',
            name='Peak',
            marker=dict(color='#f59e0b', size=12, symbol='diamond'),
            hovertemplate=f'<b>Peak</b><br>Freq: {peak_frequency:.3f} MHz<br>Power: {peak_power:.2f} dBm<extra></extra>'
        ))
        
        # Embedded statistics panel (top-right inside graph)
        stats_text = (
            f"<b>📊 Statistics</b><br>"
            f"Peak Power: {peak_power:.2f} dBm<br>"
            f"Peak Freq: {peak_frequency:.3f} MHz<br>"
            f"Average: {avg_power:.2f} dBm<br>"
            f"Integrated: {integrated_power:.2f} dBm"
        )
        
        spectrum_fig.add_annotation(
            x=0.98, y=0.98,
            xref="paper", yref="paper",
            showarrow=False,
            align="left",
            bgcolor="rgba(15,23,42,0.85)",
            bordercolor="#0ea5a9",
            borderwidth=1,
            borderpad=8,
            font=dict(color="#e6eef6", size=12),
            text=stats_text
        )
        
        # Update spectrum layout
        spectrum_fig.update_layout(
            xaxis_title='Frequency (MHz)',
            yaxis_title='Power (dBm)',
            plot_bgcolor='#020617',
            paper_bgcolor='#1e293b',
            font=dict(color='#94a3b8', size=12),
            xaxis=dict(
                gridcolor='#334155',
                showgrid=True,
                zeroline=False
            ),
            yaxis=dict(
                gridcolor='#334155',
                showgrid=True,
                zeroline=False
            ),
            hovermode='x unified',
            margin=dict(l=60, r=60, t=30, b=60),
            showlegend=False
        )
        
        # Create power integration figure with 15-minute window
        power_fig = go.Figure()
        
        if len(power_history) > 0:
            # Extract timestamps and power values from history
            times = [item[0] for item in power_history]
            powers = [item[1] for item in power_history]
            
            # Add power time series
            power_fig.add_trace(go.Scatter(
                x=times,
                y=powers,
                mode='lines+markers',
                name='Integrated Power',
                line=dict(color='#22c55e', width=2),
                marker=dict(size=4, color='#22c55e'),
                fill='tozeroy',
                fillcolor='rgba(34, 197, 94, 0.2)',
                hovertemplate='<b>Time:</b> %{x|%H:%M:%S}<br><b>Power:</b> %{y:.2f} dBm<extra></extra>'
            ))
            
            # Calculate statistics for the window
            power_avg = sum(powers) / len(powers)
            power_min = min(powers)
            power_max = max(powers)
            
            # Add average line
            power_fig.add_hline(
                y=power_avg,
                line_dash="dash",
                line_color="#06b6d4",
                line_width=1,
                annotation_text=f"Avg: {power_avg:.2f} dBm",
                annotation_position="right",
                annotation_font_color="#06b6d4",
                annotation_font_size=10
            )
            
            # Calculate time span
            time_span = (times[-1] - times[0]).total_seconds() / 60  # in minutes
            
            # Add statistics panel
            power_stats_text = (
                f"<b>📈 Power Stats ({TIME_WINDOW_MINUTES} min)</b><br>"
                f"Current: {integrated_power:.2f} dBm<br>"
                f"Average: {power_avg:.2f} dBm<br>"
                f"Min: {power_min:.2f} dBm<br>"
                f"Max: {power_max:.2f} dBm<br>"
                f"Range: {power_max - power_min:.2f} dB<br>"
                f"Samples: {len(powers)}<br>"
            )
            
            power_fig.add_annotation(
                x=0.98, y=0.98,
                xref="paper", yref="paper",
                showarrow=False,
                align="left",
                bgcolor="rgba(15,23,42,0.85)",
                bordercolor="#22c55e",
                borderwidth=1,
                borderpad=8,
                font=dict(color="#e6eef6", size=11),
                text=power_stats_text
            )
        
        # Update power integration layout
        power_fig.update_layout(
            xaxis_title='Time',
            yaxis_title='Integrated Power (dBm)',
            plot_bgcolor='#020617',
            paper_bgcolor='#1e293b',
            font=dict(color='#94a3b8', size=12),
            xaxis=dict(
                gridcolor='#334155',
                showgrid=True,
                zeroline=False,
                tickformat='%H:%M:%S'
            ),
            yaxis=dict(
                gridcolor='#334155',
                showgrid=True,
                zeroline=False
            ),
            hovermode='x unified',
            margin=dict(l=60, r=60, t=30, b=60),
            showlegend=False
        )
        
        # Get current date from timestamp
        try:
            current_date = dt.strftime('%Y-%m-%d')
            current_time = dt.strftime('%H:%M:%S')
        except:
            now = datetime.now()
            current_date = now.strftime('%Y-%m-%d')
            current_time = now.strftime('%H:%M:%S')
        
        # Update graph title with timestamp
        graph_title = f"Spectrum @ {timestamp}"
        
        # Format total power display
        total_power_display = f"{integrated_power:.2f} dBm"
        
        return (
            spectrum_fig,
            power_fig,
            current_date,
            current_time,
            f"{sweep_index + 1}/{total_sweeps}",
            str(len(frequencies)),
            filename,
            graph_title,
            total_power_display
        )
        
    except queue.Empty:
        # No new data available
        return [dash.no_update] * 9
    except Exception as e:
        print(f"Error in update_graph: {e}")
        import traceback
        traceback.print_exc()
        return [dash.no_update] * 9


@app.callback(
    [Output('play-button', 'children'),
     Output('play-button', 'style'),
     Output('is-playing', 'data'),
     Output('status-indicator', 'style'),
     Output('status-text', 'children')],
    [Input('play-button', 'n_clicks')],
    [State('is-playing', 'data')]
)
def toggle_play_pause(n_clicks, is_playing):
    global is_paused
    
    if n_clicks == 0:
        return dash.no_update
    
    new_state = not is_playing
    is_paused = not new_state
    
    if new_state:
        button_text = '⏸'
        button_style = {
            'padding': '6px 12px',
            'fontSize': '14px',
            'fontWeight': 'bold',
            'backgroundColor': '#ef4444',
            'color': 'white',
            'border': 'none',
            'borderRadius': '4px',
            'cursor': 'pointer',
            'marginRight': '6px',
            'minWidth': '36px'
        }
        indicator_style = {
            'width': '8px',
            'height': '8px',
            'borderRadius': '50%',
            'backgroundColor': '#22c55e',
            'display': 'inline-block',
            'marginRight': '6px',
            'verticalAlign': 'middle'
        }
        status_text = 'Live'
    else:
        button_text = '▶'
        button_style = {
            'padding': '6px 12px',
            'fontSize': '14px',
            'fontWeight': 'bold',
            'backgroundColor': '#22c55e',
            'color': 'white',
            'border': 'none',
            'borderRadius': '4px',
            'cursor': 'pointer',
            'marginRight': '6px',
            'minWidth': '36px'
        }
        indicator_style = {
            'width': '8px',
            'height': '8px',
            'borderRadius': '50%',
            'backgroundColor': '#ef4444',
            'display': 'inline-block',
            'marginRight': '6px',
            'verticalAlign': 'middle'
        }
        status_text = 'Paused'
    
    return button_text, button_style, new_state, indicator_style, status_text


@app.callback(
    Output('interval-component', 'n_intervals'),
    [Input('reset-button', 'n_clicks')]
)
def reset_sweep(n_clicks):
    global current_sweep_index, last_row_count, power_history, time_history
    if n_clicks > 0:
        current_sweep_index = 0
        last_row_count = 0
        # Clear the queue
        while not data_queue.empty():
            try:
                data_queue.get_nowait()
            except queue.Empty:
                break
        # Clear power history
        power_history.clear()
        time_history.clear()
        print("Reset: Cleared queue, power history, and reset sweep index")
    return 0


if __name__ == '__main__':
    import math  # Add this import for log10
    
    # Create CSV folder if it doesn't exist
    os.makedirs(CSV_FOLDER, exist_ok=True)
    
    print("=" * 60)
    print("Real-time Spectrum Analyzer Dashboard - tinySA")
    print("=" * 60)
    print(f"Monitoring directory: {CSV_FOLDER}")
    print(f"Time window: Last {TIME_WINDOW_MINUTES} minutes")
    print(f"Power calculation: Linear scale summation (CORRECT method)")
    print(f"Dashboard will be available at: http://localhost:8050")
    print("=" * 60)
    print("\nStarting CSV reader thread...")
    
    # Start CSV reader thread
    csv_reader = CSVReader(data_queue)
    csv_reader.start()
    
    print("CSV reader thread started successfully!")
    print("\nStarting Dash server...")
    print("Open your browser and navigate to: http://localhost:8050")
    print("\nPress Ctrl+C to stop the server")
    print("=" * 60)
    
    # Run the Dash app
    try:
        app.run(debug=False, host='0.0.0.0', port=8050)
    except KeyboardInterrupt:
        print("\n\nShutting down gracefully...")
        is_running = False
    finally:
        print("Dashboard stopped.")