<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor xmlns="http://www.opengis.net/sld" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:se="http://www.opengis.net/se" version="1.1.0" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:ogc="http://www.opengis.net/ogc" xsi:schemaLocation="http://www.opengis.net/sld http://schemas.opengis.net/sld/1.1.0/StyledLayerDescriptor.xsd">
  <NamedLayer>
    <se:Name>agrozonirovanie</se:Name>
    <UserStyle>
      <se:Name>agrozonirovanie</se:Name>
      <se:FeatureTypeStyle>
        <se:Rule>
          <se:Name>Слабо засушливый, тёплый климат 
(K = 0,7–0,8; Σt₁₀ = 2500–2600 °С)</se:Name>
          <se:Description>
            <se:Title>Слабо засушливый, тёплый климат 
(K = 0,7–0,8; Σt₁₀ = 2500–2600 °С)</se:Title>
          </se:Description>
          <ogc:Filter xmlns:ogc="http://www.opengis.net/ogc">
            <ogc:PropertyIsEqualTo>
              <ogc:PropertyName>Agroclimat</ogc:PropertyName>
              <ogc:Literal>Слабо засушливый, тёплый климат 
(K = 0,7–0,8; Σt₁₀ = 2500–2600 °С)</ogc:Literal>
            </ogc:PropertyIsEqualTo>
          </ogc:Filter>
          <se:PolygonSymbolizer>
            <se:Fill>
              <se:SvgParameter name="fill">#000004</se:SvgParameter>
            </se:Fill>
            <se:Stroke>
              <se:SvgParameter name="stroke">#232323</se:SvgParameter>
              <se:SvgParameter name="stroke-width">1</se:SvgParameter>
              <se:SvgParameter name="stroke-linejoin">bevel</se:SvgParameter>
            </se:Stroke>
          </se:PolygonSymbolizer>
        </se:Rule>
        <se:Rule>
          <se:Name>Слабо увлажнённый, тёплый климат
 (K = 0,8–1,0; Σt₁₀ = 2500–2600 °С)</se:Name>
          <se:Description>
            <se:Title>Слабо увлажнённый, тёплый климат
 (K = 0,8–1,0; Σt₁₀ = 2500–2600 °С)</se:Title>
          </se:Description>
          <ogc:Filter xmlns:ogc="http://www.opengis.net/ogc">
            <ogc:PropertyIsEqualTo>
              <ogc:PropertyName>Agroclimat</ogc:PropertyName>
              <ogc:Literal>Слабо увлажнённый, тёплый климат
 (K = 0,8–1,0; Σt₁₀ = 2500–2600 °С)</ogc:Literal>
            </ogc:PropertyIsEqualTo>
          </ogc:Filter>
          <se:PolygonSymbolizer>
            <se:Fill>
              <se:SvgParameter name="fill">#3b0f6f</se:SvgParameter>
            </se:Fill>
            <se:Stroke>
              <se:SvgParameter name="stroke">#232323</se:SvgParameter>
              <se:SvgParameter name="stroke-width">1</se:SvgParameter>
              <se:SvgParameter name="stroke-linejoin">bevel</se:SvgParameter>
            </se:Stroke>
          </se:PolygonSymbolizer>
        </se:Rule>
        <se:Rule>
          <se:Name>Слабо увлажнённый, тёплый климат (K = 0,8–1,0; Σt₁₀ = 2500–2600 °С)</se:Name>
          <se:Description>
            <se:Title>Слабо увлажнённый, тёплый климат (K = 0,8–1,0; Σt₁₀ = 2500–2600 °С)</se:Title>
          </se:Description>
          <ogc:Filter xmlns:ogc="http://www.opengis.net/ogc">
            <ogc:PropertyIsEqualTo>
              <ogc:PropertyName>Agroclimat</ogc:PropertyName>
              <ogc:Literal>Слабо увлажнённый, тёплый климат (K = 0,8–1,0; Σt₁₀ = 2500–2600 °С)</ogc:Literal>
            </ogc:PropertyIsEqualTo>
          </ogc:Filter>
          <se:PolygonSymbolizer>
            <se:Fill>
              <se:SvgParameter name="fill">#8c2981</se:SvgParameter>
            </se:Fill>
            <se:Stroke>
              <se:SvgParameter name="stroke">#232323</se:SvgParameter>
              <se:SvgParameter name="stroke-width">1</se:SvgParameter>
              <se:SvgParameter name="stroke-linejoin">bevel</se:SvgParameter>
            </se:Stroke>
          </se:PolygonSymbolizer>
        </se:Rule>
        <se:Rule>
          <se:Name>Слабо увлажнённый, умеренно тёплый климат 
(K = 0,8–1,0; Σt₁₀ = 2300–2500 °С)</se:Name>
          <se:Description>
            <se:Title>Слабо увлажнённый, умеренно тёплый климат 
(K = 0,8–1,0; Σt₁₀ = 2300–2500 °С)</se:Title>
          </se:Description>
          <ogc:Filter xmlns:ogc="http://www.opengis.net/ogc">
            <ogc:PropertyIsEqualTo>
              <ogc:PropertyName>Agroclimat</ogc:PropertyName>
              <ogc:Literal>Слабо увлажнённый, умеренно тёплый климат 
(K = 0,8–1,0; Σt₁₀ = 2300–2500 °С)</ogc:Literal>
            </ogc:PropertyIsEqualTo>
          </ogc:Filter>
          <se:PolygonSymbolizer>
            <se:Fill>
              <se:SvgParameter name="fill">#dd4a69</se:SvgParameter>
            </se:Fill>
            <se:Stroke>
              <se:SvgParameter name="stroke">#232323</se:SvgParameter>
              <se:SvgParameter name="stroke-width">1</se:SvgParameter>
              <se:SvgParameter name="stroke-linejoin">bevel</se:SvgParameter>
            </se:Stroke>
          </se:PolygonSymbolizer>
        </se:Rule>
        <se:Rule>
          <se:Name>Умеренно влажный, умеренно тёплый климат (
K = 1,0–1,2; Σt₁₀ = 2200–2500 °С)</se:Name>
          <se:Description>
            <se:Title>Умеренно влажный, умеренно тёплый климат (
K = 1,0–1,2; Σt₁₀ = 2200–2500 °С)</se:Title>
          </se:Description>
          <ogc:Filter xmlns:ogc="http://www.opengis.net/ogc">
            <ogc:PropertyIsEqualTo>
              <ogc:PropertyName>Agroclimat</ogc:PropertyName>
              <ogc:Literal>Умеренно влажный, умеренно тёплый климат (
K = 1,0–1,2; Σt₁₀ = 2200–2500 °С)</ogc:Literal>
            </ogc:PropertyIsEqualTo>
          </ogc:Filter>
          <se:PolygonSymbolizer>
            <se:Fill>
              <se:SvgParameter name="fill">#fe9f6d</se:SvgParameter>
            </se:Fill>
            <se:Stroke>
              <se:SvgParameter name="stroke">#232323</se:SvgParameter>
              <se:SvgParameter name="stroke-width">1</se:SvgParameter>
              <se:SvgParameter name="stroke-linejoin">bevel</se:SvgParameter>
            </se:Stroke>
          </se:PolygonSymbolizer>
        </se:Rule>
      </se:FeatureTypeStyle>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>
