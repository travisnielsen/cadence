-- =============================================================================
-- Query Templates — Wide World Importers
-- Tokens requiring manual input: %{{state_name}}%, %{{supplier_name}}%, %{{customer_name}}%
-- All other params use defaults below.
-- =============================================================================


-- 01: Application — Cities by Population in a State
-- Manual: %{{state_name}}%
SELECT TOP 10
    c.CityName,
    c.LatestRecordedPopulation,
    sp.StateProvinceName
FROM Application.Cities c
JOIN Application.StateProvinces sp
    ON c.StateProvinceID = sp.StateProvinceID
WHERE sp.StateProvinceName = '%{{state_name}}%'
    AND c.LatestRecordedPopulation IS NOT NULL
ORDER BY c.LatestRecordedPopulation DESC;


-- 02: Application — Salesperson Directory
SELECT
    FullName,
    PreferredName,
    PhoneNumber,
    EmailAddress
FROM Application.People
WHERE IsSalesperson = 1
ORDER BY FullName ASC;


-- 03: Application — Population by Sales Territory
SELECT TOP 10
    sp.SalesTerritory,
    COUNT(c.CityID) AS CityCount,
    SUM(c.LatestRecordedPopulation) AS TotalPopulation
FROM Application.StateProvinces sp
JOIN Application.Cities c
    ON sp.StateProvinceID = c.StateProvinceID
WHERE c.LatestRecordedPopulation IS NOT NULL
GROUP BY sp.SalesTerritory
ORDER BY TotalPopulation DESC;


-- 04: Purchasing — Top/Bottom Suppliers by Spend (last 90 days)
SELECT TOP 10
    s.SupplierName,
    SUM(st.AmountExcludingTax) AS TotalSpend,
    COUNT(st.SupplierTransactionID) AS TransactionCount
FROM Purchasing.Suppliers s
JOIN Purchasing.SupplierTransactions st
    ON s.SupplierID = st.SupplierID
WHERE st.TransactionDate >= DATEADD(day, -90, DATEADD(YEAR, -10, GETDATE()))
GROUP BY s.SupplierID, s.SupplierName
ORDER BY TotalSpend DESC;


-- 05: Purchasing — Purchase Orders by Supplier (last 180 days)
-- Manual: %{{supplier_name}}%
SELECT
    po.OrderDate,
    po.ExpectedDeliveryDate,
    po.IsOrderFinalized,
    s.SupplierName,
    COUNT(pol.PurchaseOrderLineID) AS LineItemCount,
    SUM(pol.OrderedOuters * pol.ExpectedUnitPricePerOuter) AS EstimatedTotal
FROM Purchasing.PurchaseOrders po
JOIN Purchasing.Suppliers s
    ON po.SupplierID = s.SupplierID
JOIN Purchasing.PurchaseOrderLines pol
    ON po.PurchaseOrderID = pol.PurchaseOrderID
WHERE s.SupplierName = '%{{supplier_name}}%'
    AND po.OrderDate >= DATEADD(day, -180, DATEADD(YEAR, -10, GETDATE()))
GROUP BY
    po.PurchaseOrderID,
    po.OrderDate,
    po.ExpectedDeliveryDate,
    po.IsOrderFinalized,
    s.SupplierName
ORDER BY po.OrderDate DESC;


-- 06: Purchasing — Supplier Order Fulfillment Status
SELECT TOP 10
    s.SupplierName,
    COUNT(pol.PurchaseOrderLineID) AS UnfinalizedLines,
    SUM(pol.OrderedOuters - pol.ReceivedOuters) AS TotalOutstandingOuters
FROM Purchasing.Suppliers s
JOIN Purchasing.PurchaseOrders po
    ON s.SupplierID = po.SupplierID
JOIN Purchasing.PurchaseOrderLines pol
    ON po.PurchaseOrderID = pol.PurchaseOrderID
WHERE pol.IsOrderLineFinalized = 0
GROUP BY s.SupplierID, s.SupplierName
ORDER BY UnfinalizedLines DESC;


-- 07: Sales — Top/Bottom Customers by Revenue (last 90 days)
SELECT TOP 10
    c.CustomerName,
    SUM(il.ExtendedPrice) AS TotalRevenue,
    COUNT(DISTINCT i.InvoiceID) AS InvoiceCount
FROM Sales.Customers c
JOIN Sales.Invoices i
    ON c.CustomerID = i.CustomerID
JOIN Sales.InvoiceLines il
    ON i.InvoiceID = il.InvoiceID
WHERE i.InvoiceDate >= DATEADD(day, -90, DATEADD(YEAR, -10, GETDATE()))
GROUP BY c.CustomerID, c.CustomerName
ORDER BY TotalRevenue DESC;


-- 08: Sales — Recent Orders for a Customer (last 180 days)
-- Manual: %{{customer_name}}%
SELECT
    o.OrderDate,
    o.ExpectedDeliveryDate,
    c.CustomerName,
    p.FullName AS Salesperson,
    COUNT(ol.OrderLineID) AS LineItemCount,
    SUM(ol.Quantity * ol.UnitPrice) AS OrderTotal
FROM Sales.Orders o
JOIN Sales.Customers c
    ON o.CustomerID = c.CustomerID
JOIN Application.People p
    ON o.SalespersonPersonID = p.PersonID
JOIN Sales.OrderLines ol
    ON o.OrderID = ol.OrderID
WHERE c.CustomerName = '%{{customer_name}}%'
    AND o.OrderDate >= DATEADD(day, -180, DATEADD(YEAR, -10, GETDATE()))
GROUP BY
    o.OrderID,
    o.OrderDate,
    o.ExpectedDeliveryDate,
    c.CustomerName,
    p.FullName
ORDER BY o.OrderDate DESC;


-- 09: Sales — Revenue by Customer Category (last 90 days)
SELECT
    cc.CustomerCategoryName,
    COUNT(DISTINCT c.CustomerID) AS CustomerCount,
    COUNT(DISTINCT i.InvoiceID) AS InvoiceCount,
    SUM(il.ExtendedPrice) AS TotalRevenue,
    SUM(il.LineProfit) AS TotalProfit
FROM Sales.CustomerCategories cc
JOIN Sales.Customers c
    ON cc.CustomerCategoryID = c.CustomerCategoryID
JOIN Sales.Invoices i
    ON c.CustomerID = i.CustomerID
JOIN Sales.InvoiceLines il
    ON i.InvoiceID = il.InvoiceID
WHERE i.InvoiceDate >= DATEADD(day, -90, DATEADD(YEAR, -10, GETDATE()))
GROUP BY cc.CustomerCategoryName
ORDER BY TotalRevenue DESC;


-- 10: Warehouse — Stock Items Below Reorder Level
SELECT TOP 20
    si.StockItemName,
    sih.QuantityOnHand,
    sih.ReorderLevel,
    sih.TargetStockLevel,
    sih.BinLocation,
    (sih.ReorderLevel - sih.QuantityOnHand) AS QuantityBelowReorder
FROM Warehouse.StockItems si
JOIN Warehouse.StockItemHoldings sih
    ON si.StockItemID = sih.StockItemID
WHERE sih.QuantityOnHand <= sih.ReorderLevel
ORDER BY QuantityBelowReorder DESC;


-- 11: Warehouse — Stock Items by Supplier
-- Manual: %{{supplier_name}}%
SELECT
    si.StockItemName,
    si.UnitPrice,
    si.RecommendedRetailPrice,
    si.Brand,
    si.Size,
    si.LeadTimeDays,
    si.IsChillerStock,
    sih.QuantityOnHand
FROM Warehouse.StockItems si
JOIN Warehouse.StockItemHoldings sih
    ON si.StockItemID = sih.StockItemID
JOIN Purchasing.Suppliers s
    ON si.SupplierID = s.SupplierID
WHERE s.SupplierName = '%{{supplier_name}}%'
ORDER BY si.StockItemName ASC;


-- 12: Warehouse — Stock Movement by Item (last 90 days)
SELECT TOP 10
    si.StockItemName,
    COUNT(sit.StockItemTransactionID) AS TransactionCount,
    SUM(ABS(sit.Quantity)) AS TotalUnitsMoved
FROM Warehouse.StockItems si
JOIN Warehouse.StockItemTransactions sit
    ON si.StockItemID = sit.StockItemID
WHERE sit.TransactionOccurredWhen >= DATEADD(day, -90, DATEADD(YEAR, -10, GETDATE()))
GROUP BY si.StockItemID, si.StockItemName
ORDER BY TotalUnitsMoved DESC;
