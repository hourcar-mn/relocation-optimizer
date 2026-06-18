# Maximize profit with integer constraints
import pulp

def solve_milp():
    # Define the problem: Maximize objective
    problem = pulp.LpProblem("MILP_Example", pulp.LpMaximize)

    # Decision variables (integer)
    x = pulp.LpVariable('x', lowBound=0, cat='Integer') # will probably use cat = 'Binary for mine project
    y = pulp.LpVariable('y', lowBound=0, cat='Integer') #we'll also use lowbound = 0
    # think of x as block and y as vehicle for SRO.

    # Objective function: Maximize 3x + 2y
    problem += 3 * x + 2 * y, "Total_Profit" # my project will be sum of score times x(i,j) across all vehicles and pods.
    # my score will prolly be something like sum of all 7 variables times their weights minus our constraints.

    # Constraints
    problem += 2 * x + y <= 8, "Material_Constraint" #only have 8 units of material. Each x uses 2 units, each y uses 1. The solver cannot exceed this.
    problem += x + 2 * y <= 6, "Labor_Constraint" # only 6 units of labor.
    #this is where all our constraints will go. I'll have to be   careful in order to capture them well.

    # Solve the problem
    try:
        problem.solve(pulp.PULP_CBC_CMD(msg=False)) # uses CBC solver, will need to look into another kind of solver.
        print("Status:", pulp.LpStatus[problem.status]) # is it optimal or infeasible (not possible given our constraints)
        print(f"Optimal x = {x.value()}, y = {y.value()}") 
        print(f"Maximum Profit = {pulp.value(problem.objective)}")
    except Exception as e:
        print("Error solving MILP:", e)

if __name__ == "__main__":
    solve_milp()

##
